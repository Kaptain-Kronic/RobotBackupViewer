"""Hidden-window probe for the 3D view: the zones, the posed arm, and a
program's taught path drawn among them.

This is the probe docs/subsystems/3d-viewer.md section 8 called the
uncomfortable part - "the viewport renders under no test at all". ui_fk_probe
pins the MATH; nothing pinned the pixels or the panel. So it asserts the
existing baseline (zones drawn, arm posed, cube snap, elevation clamp, state
restore) as well as the new program path, because the slices that follow build
on that baseline and it should not be the first thing to break.

Two backups, both fabricated:
  RB010R01B01  matched type + CURPOS, no FRAME.DG -> the arm poses (unverified)
               and follows the program: joint-recorded points exactly, cartesian
               ones through the inverse solve
  RB020R01B01  matched type + CURPOS + FRAME.DG whose world TCP CONTRADICTS the
               kinematics -> the arm must NOT be drawn, and the joint-recorded
               point must not be placed either, while cartesian points still are

Fully synthetic and identifier-clean: RB fakes under FakePlant in a temp
library, APPDATA redirected before any backupviewer import.
Run: python tests/ui_view3d_probe.py
"""
import json
import sys
import time
from pathlib import Path

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_view3d_probe_")

import webview  # noqa: E402

from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

GOOD = "RB010R01B01"          # poses honestly
BENT = "RB020R01B01"          # kinematics contradict the backup's own report
ROBOT_TYPE = "ARC Mate 120iD"  # a shipped builtin chain
PROG = "MOVER.LS"

# joints the fabricated CURPOS reports, and the joint-recorded taught point
CURPOS_Q = [10.0, -15.0, 8.0, 0.0, -30.0, 45.0]
TAUGHT_Q = [25.0, -30.0, 20.0, 0.0, -40.0, 10.0]


def dcspos() -> str:
    """One enabled keep-out zone - enough geometry for faces to be drawn."""
    n, xs, ys = 1, [-1500.0, 1500.0, 1500.0, -1500.0], [-1500.0, -1500.0, 1500.0, 1500.0]
    out = ["[*SYSTEM*]$DCSS_CPC  Storage: SHADOW  Access: RW  : ARRAY[32] OF DCSS_CPC_T",
           f"     Field: $DCSS_CPC[{n}].$COMMENT Access: RW: STRING[25] = 'CellFence'",
           f"     Field: $DCSS_CPC[{n}].$ENABLE Access: RW: INTEGER = 1",
           f"     Field: $DCSS_CPC[{n}].$MODE Access: RW: INTEGER = 1",
           f"     Field: $DCSS_CPC[{n}].$GRP_NUM Access: RW: INTEGER = 1",
           f"     Field: $DCSS_CPC[{n}].$MODEL_NUM  ARRAY[3] OF INTEGER"]
    out += [f"      [{i + 1}] = {v}" for i, v in enumerate((-1, 0, 0))]
    out += [f"     Field: $DCSS_CPC[{n}].$UFRM_NUM Access: RW: INTEGER = 0",
            f"     Field: $DCSS_CPC[{n}].$NUM_VTX Access: RW: INTEGER = 4",
            f"     Field: $DCSS_CPC[{n}].$X  ARRAY[8] OF REAL"]
    out += [f"      [{i + 1}] = {v:e}" for i, v in enumerate(xs + [0.0] * 4)]
    out += [f"     Field: $DCSS_CPC[{n}].$Y  ARRAY[8] OF REAL"]
    out += [f"      [{i + 1}] = {v:e}" for i, v in enumerate(ys + [0.0] * 4)]
    out += [f"     Field: $DCSS_CPC[{n}].$Z1 Access: RW: REAL = {0.0:e}",
            f"     Field: $DCSS_CPC[{n}].$Z2 Access: RW: REAL = {2500.0:e}",
            f"     Field: $DCSS_CPC[{n}].$STOP_TYP Access: RW: INTEGER = 0",
            f"     Field: $DCSS_CPC[{n}].$USE_PREDICT Access: RW: INTEGER = 1"]
    return "\n".join(out) + "\n"


DCSVRFY = f"""DATE: 01-JAN-26 12:00
DCS Version: V9.30

--- Robot Setup ---
Robot: {ROBOT_TYPE}
Group: 1
"""


def curpos() -> str:
    rows = "\n".join(f"Joint  {i + 1}:  {v}" for i, v in enumerate(CURPOS_Q))
    return (f"DATE: 01-JAN-26 12:00\n\nGroup #: 1\n{rows}\n"
            "Tool #:  1\nCURRENT WORLD POSITION:\n"
            "  X:  1200.0\n  Y:  0.0\n  Z:  900.0\n"
            "  W:  0.0\n  P:  0.0\n  R:  0.0\n")


# A taught tool plus a world TCP that cannot both be true of this chain: the
# calibration gate must catch it and the arm must not be drawn.
FRAME_DG = """Tool Frame
   0.0    0.0   150.0    0.0    0.0    0.0  GRIPPER
"""

PROGRAM = f"""/PROG  MOVER
/ATTR
COMMENT		= "probe path";
/MN
   1:J P[1] 100% FINE ;
   2:L P[2] 500mm/sec CNT50 ;
   3:L P[3] 300mm/sec FINE ;
   4:J P[9] 100% FINE ;
/POS
P[1]{{
   GP1:
	UF : 0, UT : 0,		CONFIG : 'N U T, 0, 0, 0',
	X =  1200.000  mm,	Y =  -400.000  mm,	Z =  900.000  mm,
	W =  0.000 deg,	P =  0.000 deg,	R =  0.000 deg
}};
P[2]{{
   GP1:
	UF : 0, UT : 0,		CONFIG : 'N U T, 0, 0, 0',
	X =  1200.000  mm,	Y =  400.000  mm,	Z =  900.000  mm,
	W =  0.000 deg,	P =  0.000 deg,	R =  0.000 deg
}};
P[3]{{
   GP1:
	UF : 0, UT : 0,
	{",	".join(f"J{i + 1}=  {v:.3f} deg" for i, v in enumerate(TAUGHT_Q))}
}};
/END
"""


def build_tree(lib: Path) -> None:
    line = lib / "FakePlant" / "LINE01"
    for rb, bent in ((GOOD, False), (BENT, True)):
        snap = line / rb / "2026_01_01" / "12_00_00"
        snap.mkdir(parents=True)
        (snap / "SUMMARY.DG").write_text(f"Robot: {rb}\n", encoding="utf-8")
        (snap / "DCSPOS.VA").write_text(dcspos(), encoding="utf-8")
        (snap / "DCSVRFY.DG").write_text(DCSVRFY, encoding="utf-8")
        (snap / "CURPOS.DG").write_text(curpos(), encoding="utf-8")
        (snap / PROG).write_text(PROGRAM, encoding="utf-8")
        if bent:
            # a taught tool + the world TCP above cannot both hold for this
            # chain at these joints -> measure_flange refuses
            (snap / "FRAME.DG").write_text(FRAME_DG, encoding="utf-8")
        (snap / "backup.json").write_text(
            json.dumps({"robot": rb, "line": "LINE01", "plant": "FakePlant",
                        "taken": "2026-01-01T12:00:00", "complete": True}),
            encoding="utf-8")


def open_robot(window, name, prev_sid=""):
    """Click the library row like a user, then wait for the manifest to
    actually CHANGE - polling for any truthy sid races and cheerfully returns
    the still-open previous backup's (ui_tabs_probe paid for this one)."""
    clicked = poll(window, """(function(){
        var row=[...document.querySelectorAll('.lib-robot')].find(function(r){
            return r.textContent.indexOf('%s')>=0;});
        if (!row) return '';
        row.click();
        return 'y';
    })()""" % name)
    if clicked != "y":
        return ""
    return poll(window,
                "BV.state.manifest && BV.state.manifest.sid && "
                "BV.state.manifest.sid !== %s ? BV.state.manifest.sid : ''"
                % json.dumps(prev_sid))


def goto(window, hash_):
    js(window, "location.hash = %s" % json.dumps(hash_))
    time.sleep(0.6)


def probe(window):
    try:
        time.sleep(4)  # boot

        sid = open_robot(window, GOOD)
        check("open.backup", bool(sid), f"(sid={sid!r})")

        # ---------- the baseline this feature builds on ----------
        goto(window, "#view3d")
        faces = poll(window, "document.querySelectorAll('.v3-face').length")
        check("view3d.zones_drawn", faces > 0, f"(got {faces} faces)")

        skel = poll(window, "document.querySelectorAll('.v3-skel').length")
        check("view3d.arm_posed", skel == 1, f"(got {skel} skeletons)")
        d = js(window, "(document.querySelector('.v3-skel')||{}).getAttribute "
                       "? document.querySelector('.v3-skel').getAttribute('d') : ''")
        check("view3d.arm_has_geometry", bool(d) and d.count("L") >= 5, f"(d={str(d)[:40]!r})")

        # the five scene groups, in the order the painter's sort depends on
        order = js(window, "[...document.querySelector('.v3-svg').children]"
                           ".map(function(g){return g.getAttribute('class');}).join(',')")
        check("view3d.layer_order",
              order == "v3-l-base,v3-l-path,v3-l-arm,v3-l-zone,v3-l-wire", f"({order})")

        # the cube snaps the camera and refits
        az0 = js(window, "BV.tabState('view3d').az")
        # an SVG element has no HTMLElement.click(), and the cube handler is
        # delegated from the overlay root - so dispatch a bubbling event
        js(window, "(function(){var t=document.querySelector('.v3-cube [data-az]');"
                   "if(t) t.dispatchEvent(new MouseEvent('click',{bubbles:true}));})()")
        time.sleep(0.4)
        az1 = js(window, "BV.tabState('view3d').az")
        check("view3d.cube_snaps", az1 != az0, f"({az0} -> {az1})")
        check("view3d.cube_refits", js(window, "BV.tabState('view3d').box === null"))

        # elevation stops exactly at the pole, however it got out of range
        js(window, "BV.tabState('view3d').el = 500; BV.route()")
        time.sleep(0.6)
        el = js(window, "BV.tabState('view3d').el")
        check("view3d.el_clamps", el == 90, f"(got {el})")

        # ---------- the program path ----------
        goto(window, "#view3d/" + PROG)
        steps = poll(window, "document.querySelectorAll('.v3-step').length")
        check("prog.steps_listed", steps == 4, f"(got {steps})")

        pathd = poll(window, "(document.querySelector('.v3-path')||{}).getAttribute"
                             " ? document.querySelector('.v3-path').getAttribute('d') : ''")
        check("prog.path_drawn", bool(pathd) and "L" in str(pathd), f"(d={str(pathd)[:40]!r})")

        # every placed step gets a marker, and only those - the INVARIANT, so
        # this check keeps meaning something when the fixture program grows
        pts = js(window, "document.querySelectorAll('.v3-pt').length")
        placed = js(window, "document.querySelectorAll('.v3-step:not(.dim)').length")
        check("prog.points_match_placed", pts == placed and pts > 0,
              f"(pts={pts} placed={placed})")

        # the joint-recorded point placed through the same FK the arm uses
        check("prog.joint_point_placed",
              js(window, "[...document.querySelectorAll('.v3-step')][2]"
                         ".className.indexOf('dim') < 0"))
        # the inverse solve lands second - wait for it, then read the tiers
        poll(window, "[...document.querySelectorAll('.v3-step-tags')]"
                     ".map(function(t){return t.textContent;}).join('|')"
                     ".indexOf('solved') >= 0 ? 'y' : ''")
        tags = js(window, "[...document.querySelectorAll('.v3-step')]"
                          ".map(function(r){var t=r.querySelector('.v3-step-tags');"
                          "return t?t.textContent:'';}).join('|')")
        check("prog.joint_point_exact", "exact" in str(tags), f"({tags})")
        check("prog.cartesian_point_solved", "solved" in str(tags), f"({tags})")

        # a move whose position the program does not record is LISTED, dimmed,
        # and says why - never silently dropped
        refused = js(window, "[...document.querySelectorAll('.v3-step.dim')]"
                             ".map(function(r){return r.title;}).join('|')")
        check("prog.refused_listed", "P[9]" in str(refused), f"({str(refused)[:80]!r})")
        check("prog.refused_note",
              "no /POS entry" in str(refused) or "records that position" in str(refused))

        # the viewport says so too
        notes = js(window, "[...document.querySelectorAll('.v3-note')]"
                           ".map(function(t){return t.textContent;}).join('|')")
        check("prog.viewport_note", "not placed" in str(notes), f"({str(notes)[:100]!r})")

        # every placed step still gets a marker once the solve has landed -
        # a point the arm cannot reach is still where the backup says it is
        pts2 = js(window, "document.querySelectorAll('.v3-pt').length")
        placed2 = js(window, "document.querySelectorAll('.v3-step:not(.dim)').length")
        check("prog.markers_survive_the_solve", pts2 == placed2 and pts2 == pts,
              f"(pts={pts2} placed={placed2})")

        # picking a step moves the selection in the list, in the viewport, AND
        # moves the arm: this is the whole point of the slice
        arm0 = js(window, "document.querySelector('.v3-skel').getAttribute('d')")
        js(window, "[...document.querySelectorAll('.v3-step')][1].click()")
        time.sleep(0.5)
        check("prog.step_click_selects",
              js(window, "BV.tabState('view3d').step") == 1)
        check("prog.selected_point_marked",
              js(window, "document.querySelectorAll('.v3-pt.sel').length") == 1)
        arm1 = js(window, "document.querySelector('.v3-skel').getAttribute('d')")
        check("pose.arm_follows_the_step", arm0 != arm1)
        check("pose.pill_says_program",
              "program" in str(js(window, "[...document.querySelectorAll('.v3-row')]"
                                          ".map(function(r){return r.textContent;})"
                                          ".join('|')")))

        # step 1 is a cartesian point, so its detail carries the solve itself
        detail = js(window, "document.querySelector('.v3-prog-detail').textContent")
        check("pose.residual_shown", "Solve residual" in str(detail), f"({str(detail)[:80]!r})")
        check("pose.says_how_it_was_posed", "Posed by" in str(detail))
        check("pose.branch_note",
              "CONFIG" in str(js(window, "[...document.querySelectorAll('.v3-note')]"
                                         ".map(function(t){return t.textContent;})"
                                         ".join('|')")))

        # typing in the pose grid takes the arm back off the program
        js(window, """(function(){
            var rows=[...document.querySelectorAll('.v3-row-head')];
            var r=rows.find(function(h){return h.textContent.indexOf('pose')>=0;});
            if (r) r.click();
        })()""")
        time.sleep(0.4)
        js(window, """(function(){
            var i=document.querySelector('.v3-pose-cell input');
            if(!i) return; i.value='33';
            i.dispatchEvent(new Event('change',{bubbles:true}));
        })()""")
        time.sleep(0.5)
        check("pose.manual_takes_over",
              "manual" in str(js(window, "[...document.querySelectorAll('.v3-row')]"
                                         ".map(function(r){return r.textContent;})"
                                         ".join('|')")))
        check("pose.path_survives_manual",
              js(window, "document.querySelectorAll('.v3-path').length") == 1)
        # back to the program
        goto(window, "#overview")
        goto(window, "#view3d/" + PROG)
        poll(window, "document.querySelectorAll('.v3-step').length")

        # the picker filters
        js(window, "[...document.querySelectorAll('#toolbar .btn')]"
                   ".find(function(b){return b.textContent.indexOf('\\u25be')>=0;}).click()")
        time.sleep(0.6)
        n_all = poll(window, "document.querySelectorAll('.v3-pick-item').length")
        check("prog.picker_lists", n_all >= 1, f"(got {n_all})")
        js(window, "(function(){var f=document.querySelector('.v3-pick input');"
                   "f.value='ZZZNOMATCH';f.dispatchEvent(new Event('input'));})()")
        time.sleep(0.3)
        check("prog.picker_filters",
              js(window, "document.querySelectorAll('.v3-pick-item').length") == 0)
        js(window, "document.body.click()")
        time.sleep(0.3)

        # the toggles hide the path and the markers
        js(window, "[...document.querySelectorAll('#toolbar .btn')]"
                   ".find(function(b){return b.textContent==='points';}).click()")
        time.sleep(0.4)
        check("prog.points_toggle",
              js(window, "document.querySelectorAll('.v3-pt').length") == 0)

        # state survives leaving and coming back
        goto(window, "#overview")
        goto(window, "#view3d/" + PROG)
        check("view3d.state_restores",
              js(window, "BV.tabState('view3d').showPoints") is False and
              js(window, "BV.tabState('view3d').az") == az1)

        # ---------- the gate: contradiction means no arm, path still drawn ----
        js(window, "BV.goHome()")
        time.sleep(0.8)
        sid2 = open_robot(window, BENT, prev_sid=sid)
        check("open.bent_backup", bool(sid2) and sid2 != sid, f"(sid={sid2!r})")
        goto(window, "#view3d/" + PROG)

        check("gate.no_arm",
              js(window, "document.querySelectorAll('.v3-skel').length") == 0)
        check("gate.path_still_drawn",
              js(window, "document.querySelectorAll('.v3-path').length") == 1)
        check("gate.joint_point_refused",
              js(window, "[...document.querySelectorAll('.v3-step')][2]"
                         ".className.indexOf('dim') >= 0"))
        bentnotes = js(window, "[...document.querySelectorAll('.v3-note')]"
                               ".map(function(t){return t.textContent;}).join('|')")
        check("gate.mismatch_note", "mismatch" in str(bentnotes),
              f"({str(bentnotes)[:120]!r})")

        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass


def main():
    lib = _TMP / "lib"
    build_tree(lib)
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
    window._bv_api = api
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
