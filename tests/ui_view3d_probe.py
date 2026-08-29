"""Hidden-window probe for the 3D view: the zones, the posed arm, and a
program's taught path drawn among them.

This is the probe docs/subsystems/3d-viewer.md section 8 called the
uncomfortable part - "the viewport renders under no test at all". ui_fk_probe
pins the MATH; nothing pinned the pixels or the panel. So it asserts the
existing baseline (zones drawn, arm posed, cube snap, elevation clamp, state
restore) as well as the new program path, because the slices that follow build
on that baseline and it should not be the first thing to break.

Three backups, all fabricated:
  RB010R01B01  matched type + CURPOS, no FRAME.DG -> the arm poses (unverified)
               and follows the program: joint-recorded points exactly, cartesian
               ones through the inverse solve
  RB020R01B01  matched type + CURPOS + FRAME.DG whose world TCP CONTRADICTS the
               kinematics -> the arm must NOT be drawn, and the joint-recorded
               point must not be placed either, while cartesian points still are
  RC010R01B01  zones but no robot-setup line at all, so no type and no chain -
               the commonest real case (two of the four pinned sample backups
               are exactly this). Cartesian points and the path still draw,
               because that composition never needed the kinematics

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
BARE = "RC010R01B01"          # no robot type in the report -> no chain at all
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

# Two taught tools, because that is the shape that made the filter necessary:
# a drop program runs most of its moves on one tool and a couple on another,
# and the TCP is a different physical point on each.
SYSFRAME = """[*SYSTEM*]$MNUTOOL  Storage: CMOS  Access: RW  : ARRAY[2,20] OF POSITION
  [1,1] = '' Group: 1
    X:   0.000   Y:   0.000   Z: 150.000
    W:   0.000   P:   0.000   R:   0.000
  [1,11] = '' Group: 1
    X:   0.000   Y: 200.000   Z: 300.000
    W:   0.000   P:   0.000   R:   0.000
[*SYSTEM*]$MNUTOOLNUM  Storage: CMOS  Access: RW  : ARRAY[2] OF BYTE
  [1] = 1
[*SYSTEM*]$MNUFRAMENUM  Storage: CMOS  Access: RW  : ARRAY[2] OF BYTE
  [1] = 0
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
	UF : 0, UT : 1,		CONFIG : 'N U T, 0, 0, 0',
	X =  1200.000  mm,	Y =  -400.000  mm,	Z =  900.000  mm,
	W =  0.000 deg,	P =  0.000 deg,	R =  0.000 deg
}};
P[2]{{
   GP1:
	UF : 0, UT : 1,		CONFIG : 'N U T, 0, 0, 0',
	X =  1200.000  mm,	Y =  400.000  mm,	Z =  900.000  mm,
	W =  0.000 deg,	P =  0.000 deg,	R =  0.000 deg
}};
P[3]{{
   GP1:
	UF : 0, UT : 11,
	{",	".join(f"J{i + 1}=  {v:.3f} deg" for i, v in enumerate(TAUGHT_Q))}
}};
/END
"""


# the same report with no robot-setup section: nothing names a type, so
# modeldb has nothing to match and the arm stays honestly absent
DCSVRFY_UNTYPED = """DATE: 01-JAN-26 12:00
DCS Version: V9.30
"""


# Filler so the picker has a list worth scrolling: 30 listings that carry a
# taught point and 10 pure-logic ones that do not. The count matters - the flex
# crush this fixture pins only appears once the rows overflow the box.
N_WITH_POINTS = 30
N_LOGIC_ONLY = 10


def filler(i: int, with_point: bool) -> str:
    body = "   1:J P[1] 100% FINE ;\n" if with_point else "   1:  DO[%d]=ON ;\n" % (i + 1)
    pos = ("P[1]{\n   GP1:\n\tUF : 0, UT : 0,\n"
           "\tX =  900.000  mm,\tY =  0.000  mm,\tZ =  800.000  mm,\n"
           "\tW =  0.000 deg,\tP =  0.000 deg,\tR =  0.000 deg\n};\n"
           if with_point else "")
    return ("/PROG  %s\n/ATTR\nCOMMENT\t\t= \"filler %d\";\n/MN\n%s/POS\n%s/END\n"
            % (("PATHFILL%02d" % i) if with_point else ("LOGICONLY%02d" % i), i, body, pos))


def build_tree(lib: Path) -> None:
    line = lib / "FakePlant" / "LINE01"
    for rb, bent in ((GOOD, False), (BENT, True), (BARE, False)):
        snap = line / rb / "2026_01_01" / "12_00_00"
        snap.mkdir(parents=True)
        (snap / "SUMMARY.DG").write_text(f"Robot: {rb}\n", encoding="utf-8")
        (snap / "DCSPOS.VA").write_text(dcspos(), encoding="utf-8")
        (snap / "DCSVRFY.DG").write_text(
            DCSVRFY_UNTYPED if rb == BARE else DCSVRFY, encoding="utf-8")
        (snap / "CURPOS.DG").write_text(curpos(), encoding="utf-8")
        (snap / "SYSFRAME.VA").write_text(SYSFRAME, encoding="utf-8")
        (snap / PROG).write_text(PROGRAM, encoding="utf-8")
        for k in range(N_WITH_POINTS):
            (snap / ("PATHFILL%02d.LS" % k)).write_text(filler(k, True), encoding="utf-8")
        for k in range(N_LOGIC_ONLY):
            (snap / ("LOGICONLY%02d.LS" % k)).write_text(filler(k, False), encoding="utf-8")
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
        # delegated from the component's own svg root - so dispatch a bubbling
        # event. The cube is BV.viewCube now, on `svg.viewcube`, not the old
        # inline `.v3-cube` group this used to reach into.
        js(window, "(function(){var t=document.querySelector('.viewcube [data-az]');"
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

        # ---------- playback ----------
        # THE check this probe exists for on this slice: the hidden WebView2 a
        # probe runs in has no requestAnimationFrame at all, so the loop's
        # fallback path is the only one it can ever exercise - and a plant PC
        # on the software-rendering rescue path is not far from that.
        # Force the fallback: HAS_RAF is read once when the tab renders, so
        # rAF has to be gone BEFORE the re-route, not after. A plant PC on the
        # software-rendering rescue path is not far from this, and the probe's
        # own hidden window may have no rAF at all.
        js(window, "window.__raf = window.requestAnimationFrame;"
                   "window.requestAnimationFrame = undefined;")
        goto(window, "#overview")
        goto(window, "#view3d/" + PROG)
        poll(window, "document.querySelector('.v3-player') && "
                     "!document.querySelector('.v3-player').hidden ? 'y' : ''")
        check("play.no_raf_path",
              js(window, "typeof window.requestAnimationFrame") != "function")
        check("play.bar_shown",
              js(window, "!document.querySelector('.v3-player').hidden"))
        total = js(window, "document.querySelector('.v3-clock').textContent")
        check("play.clock_has_a_total", "/" in str(total) and "0.0 s" != str(total),
              f"({total})")

        armA = js(window, "document.querySelector('.v3-skel').getAttribute('d')")
        fit0 = js(window, "JSON.stringify(document.querySelector('.v3-svg')._fitBox)")
        js(window, "document.querySelector('.v3-player .v3-pb').click()")   # play
        moved = poll(window,
                     "document.querySelector('.v3-skel').getAttribute('d') !== "
                     + json.dumps(str(armA)) + " ? 'y' : ''", tries=40, delay=0.25)
        check("play.advances_the_arm", moved == "y")
        check("play.button_flips",
              js(window, "document.querySelector('.v3-player .v3-pb').textContent") != "\u25b6")

        # the fit must NOT breathe while the arm moves - it was sized for the
        # whole run before the first frame
        fit1 = js(window, "JSON.stringify(document.querySelector('.v3-svg')._fitBox)")
        check("play.fit_does_not_breathe", fit0 == fit1, f"({fit0} -> {fit1})")

        # pause holds where it is
        js(window, "document.querySelector('.v3-player .v3-pb').click()")
        time.sleep(0.5)
        held = js(window, "document.querySelector('.v3-skel').getAttribute('d')")
        time.sleep(1.2)
        check("play.pause_holds",
              js(window, "document.querySelector('.v3-skel').getAttribute('d')") == held)
        check("play.button_back_to_play",
              js(window, "document.querySelector('.v3-player .v3-pb').textContent")
              == "\u25b6")

        # the scrubber seeks, and the stop button rewinds
        js(window, """(function(){
            var r=document.querySelector('.v3-scrub');
            r.value='900'; r.dispatchEvent(new Event('input',{bubbles:true}));
        })()""")
        time.sleep(0.5)
        late = js(window, "document.querySelector('.v3-skel').getAttribute('d')")
        check("play.scrubber_seeks", late != held)
        js(window, "[...document.querySelectorAll('.v3-player .v3-pb')][1].click()")
        time.sleep(0.5)
        check("play.stop_rewinds", js(window, "BV.tabState('view3d').t") == 0)

        # the live tcp marker rides the tool centre while the arm moves
        check("play.live_tcp_marker",
              js(window, "document.querySelectorAll('.v3-tcp-live').length") == 1)
        check("play.preview_note",
              "cycle-time" in str(js(window, "[...document.querySelectorAll('.v3-note')]"
                                             ".map(function(t){return t.textContent;})"
                                             ".join('|')")))

        # hand requestAnimationFrame back before anything else runs
        js(window, "window.requestAnimationFrame = window.__raf;")
        goto(window, "#overview")
        goto(window, "#view3d/" + PROG)
        poll(window, "document.querySelectorAll('.v3-step').length")
        poll(window, "[...document.querySelectorAll('.v3-step-tags')]"
                     ".map(function(t){return t.textContent;}).join('|')"
                     ".indexOf('solved') >= 0 ? 'y' : ''")

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
        check("pose.manual_hides_the_player",
              js(window, "document.querySelector('.v3-player').hidden"))
        # back to the program
        goto(window, "#overview")
        goto(window, "#view3d/" + PROG)
        poll(window, "document.querySelectorAll('.v3-step').length")

        # ---------- the picker (no program loaded) ----------
        # a bare #view3d deliberately RESTORES the last program, so the way
        # back to the picker is the section's own "change" button
        js(window, """(function(){
            var b=[...document.querySelectorAll('.v3-cat .btn')]
                .find(function(x){return x.textContent==='change';});
            if (b) b.click();
        })()""")
        time.sleep(0.8)
        check("pick.change_returns_to_the_picker",
              js(window, "document.querySelectorAll('.v3-pick').length") == 1)
        check("pick.change_clears_the_program",
              js(window, "BV.tabState('view3d').prog") is None)
        n_shown = poll(window, "document.querySelectorAll('.v3-pick-item').length")
        # MOVER plus the fillers that carry a point; the logic-only ones are out
        check("pick.only_programs_with_points", n_shown == 1 + N_WITH_POINTS,
              f"(got {n_shown}, wanted {1 + N_WITH_POINTS})")
        names = js(window, "[...document.querySelectorAll('.v3-pick-name')]"
                           ".map(function(n){return n.textContent;}).join(',')")
        check("pick.logic_only_excluded", "LOGICONLY" not in str(names))

        # The regression this list was rebuilt for: on a real controller's
        # library the rows ran together into a wall of clipped, overlapping
        # text. Pinned as the invariant a reader actually needs - every row
        # keeps a full line box, and no row sits on top of the one above it -
        # rather than as any one CSS mechanism, since the density had several
        # contributing causes and a future one would look the same.
        geom = js(window, """(function(){
            var rs=[...document.querySelectorAll('.v3-pick-item')]
                .map(function(r){return r.getBoundingClientRect();});
            var minh=Infinity, overlap=0;
            for (var i=0;i<rs.length;i++){
                if (rs[i].height < minh) minh = rs[i].height;
                if (i && rs[i].top < rs[i-1].bottom - 0.5) overlap++;
            }
            return minh + '|' + overlap + '|' + rs.length;
        })()""")
        minh, overlap, nrows = str(geom).split("|")
        check("pick.rows_keep_a_full_line", float(minh) >= 12,
              f"(shortest row {minh}px over {nrows} rows)")
        check("pick.rows_do_not_overlap", int(overlap) == 0,
              f"({overlap} of {nrows} rows sit on the one above)")
        check("pick.list_scrolls_instead",
              js(window, "(function(){var l=document.querySelector('.v3-pick-list');"
                         "return l.scrollHeight > l.clientHeight;})()"))

        # the count of taught points rides each row - why it is offered at all
        check("pick.shows_point_counts",
              js(window, "document.querySelectorAll('.v3-pick-n').length") == n_shown)

        # "show all" reaches the ones with nothing to draw, so nothing is hidden
        js(window, """(function(){
            var b=[...document.querySelectorAll('.v3-cat .btn')]
                .find(function(x){return x.textContent==='show all';});
            if (b) b.click();
        })()""")
        time.sleep(0.5)
        n_every = js(window, "document.querySelectorAll('.v3-pick-item').length")
        check("pick.show_all_reveals_the_rest", n_every == n_shown + N_LOGIC_ONLY,
              f"(got {n_every}, wanted {n_shown + N_LOGIC_ONLY})")
        js(window, """(function(){
            var b=[...document.querySelectorAll('.v3-cat .btn')]
                .find(function(x){return x.textContent==='with points';});
            if (b) b.click();
        })()""")
        time.sleep(0.5)

        # the filter narrows, and typing does not cost the caret
        js(window, "(function(){var f=document.querySelector('.v3-pick input');"
                   "f.value='MOVER';f.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        check("pick.filter_narrows",
              js(window, "document.querySelectorAll('.v3-pick-item').length") == 1)
        check("pick.filter_keeps_its_text",
              js(window, "document.querySelector('.v3-pick input').value") == "MOVER")
        js(window, "(function(){var f=document.querySelector('.v3-pick input');"
                   "f.value='ZZZNOMATCH';f.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        check("pick.no_match_is_said",
              js(window, "document.querySelectorAll('.v3-pick-item').length") == 0 and
              "no program matches" in
              str(js(window, "document.querySelector('.v3-pick-note').textContent")))

        # clear the filter first, or the list is still showing no-match and
        # the click below lands on nothing (which would let the next check
        # pass for the wrong reason - there being no program at all)
        js(window, "(function(){var f=document.querySelector('.v3-pick input');"
                   "f.value='';f.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)

        # a program on ONE tool gets no filter - a checkbox that can never
        # change anything is noise, and this surface vanishes when unusable
        js(window, """(function(){
            var b=[...document.querySelectorAll('.v3-pick-item')]
                .find(function(x){return x.textContent.indexOf('PATHFILL00')>=0;});
            if (b) b.click();
        })()""")
        time.sleep(1.2)
        poll(window, "document.querySelectorAll('.v3-step').length")
        check("tool.single_tool_program_loaded",
              "PATHFILL00" in str(js(window, "(document.querySelector('.v3-prog-name')"
                                             "||{}).textContent || ''")))
        check("tool.single_tool_gets_no_filter",
              js(window, "document.querySelectorAll('.v3-tools').length") == 0)
        check("tool.single_tool_still_shows_its_cell",
              js(window, "document.querySelectorAll('.v3-step-ft').length") == 1)
        js(window, """(function(){
            var b=[...document.querySelectorAll('.v3-cat .btn')]
                .find(function(x){return x.textContent==='change';});
            if (b) b.click();
        })()""")
        time.sleep(0.8)

        # clicking one loads it
        js(window, "(function(){var f=document.querySelector('.v3-pick input');"
                   "f.value='';f.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        js(window, """(function(){
            var b=[...document.querySelectorAll('.v3-pick-item')]
                .find(function(x){return x.textContent.indexOf('MOVER')>=0;});
            if (b) b.click();
        })()""")
        time.sleep(1.0)
        check("pick.click_loads_the_program",
              js(window, "BV.tabState('view3d').prog") == PROG)
        poll(window, "document.querySelectorAll('.v3-step').length")

        # ---------- the frame/tool cell, and one tool at a time ----------
        fts = js(window, "[...document.querySelectorAll('.v3-step-ft')]"
                         ".map(function(n){return n.textContent;}).join(',')")
        check("tool.row_shows_uf_and_ut", "uf0/ut1" in str(fts) and "uf0/ut11" in str(fts),
              f"({fts})")

        check("tool.filter_offered", js(window, "document.querySelectorAll('.v3-tool').length") == 2)
        pts_all = js(window, "document.querySelectorAll('.v3-pt').length")
        total_all = js(window, "document.querySelector('.v3-clock').textContent")

        # drop the odd tool out: its move stays LISTED but leaves the run
        js(window, "document.querySelector('.v3-tool[data-ut=\"11\"] input').click()")
        time.sleep(0.8)
        check("tool.excluded_move_stays_listed",
              js(window, "document.querySelectorAll('.v3-step').length") == 4)
        check("tool.excluded_move_reads_excluded",
              js(window, "document.querySelectorAll('.v3-step.offtool').length") == 1)
        check("tool.excluded_point_leaves_the_path",
              js(window, "document.querySelectorAll('.v3-pt').length") == pts_all - 1,
              f"(was {pts_all})")
        check("tool.run_gets_shorter",
              js(window, "document.querySelector('.v3-clock').textContent") != total_all,
              f"(was {total_all})")
        check("tool.says_it_is_skipping",
              "skipping the moves taught with ut11" in
              str(js(window, "document.querySelector('.v3-prog-notes').textContent")))
        check("tool.says_the_join_is_not_a_real_path",
              "not a path the" in
              str(js(window, "document.querySelector('.v3-prog-notes').textContent")))

        # the last tool cannot be turned off - that is an empty run, not a filter
        js(window, "document.querySelector('.v3-tool[data-ut=\"1\"] input').click()")
        time.sleep(0.8)
        check("tool.cannot_exclude_every_tool",
              js(window, "document.querySelectorAll('.v3-pt').length") > 0)

        # back to everything
        js(window, """(function(){
            [...document.querySelectorAll('.v3-tool input')].forEach(function(i){
                if (!i.checked) i.click(); });
        })()""")
        time.sleep(0.8)
        check("tool.restores",
              js(window, "document.querySelectorAll('.v3-pt').length") == pts_all)

        # the picker is the panel's job alone - a program button back in the
        # toolbar would be a second door into the same list
        tools = js(window, "[...document.querySelectorAll('#toolbar .btn')]"
                           ".map(function(b){return b.textContent;}).join('|')")
        check("pick.no_second_door_in_the_toolbar",
              "program" not in str(tools).lower(), f"({tools})")

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

        # ---------- no kinematics at all: the commonest real case ----------
        js(window, "BV.goHome()")
        time.sleep(0.8)
        sid3 = open_robot(window, BARE, prev_sid=sid2)
        check("open.untyped_backup", bool(sid3) and sid3 != sid2)
        goto(window, "#view3d/" + PROG)
        poll(window, "document.querySelectorAll('.v3-step').length")

        check("bare.zones_still_drawn",
              js(window, "document.querySelectorAll('.v3-face').length") > 0)
        check("bare.no_arm",
              js(window, "document.querySelectorAll('.v3-skel').length") == 0)
        check("bare.cartesian_path_drawn",
              js(window, "document.querySelectorAll('.v3-path').length") == 1)
        # the two cartesian points place; the joint-recorded one cannot
        check("bare.two_points_placed",
              js(window, "document.querySelectorAll('.v3-pt').length") == 2)
        bare_notes = js(window, "[...document.querySelectorAll('.v3-step')]"
                                ".map(function(r){return r.title;}).join('|')")
        check("bare.joint_point_says_why", "kinematics" in str(bare_notes),
              f"({str(bare_notes)[:110]!r})")
        check("bare.no_player",
              js(window, "document.querySelector('.v3-player').hidden"))
        # and nothing claims a posture it did not solve
        vp_notes = js(window, "[...document.querySelectorAll('.v3-note')]"
                              ".map(function(t){return t.textContent;}).join('|')")
        check("bare.no_branch_claim", "CONFIG" not in str(vp_notes), f"({vp_notes})")

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
