"""Hidden-window probe for the CV-X camera screens: the camera overview
(crossfade hero slot + summary cards) and the camera 3d view (model rail,
canvas mesh viewer, orientation cube + snap views, perspective toggle,
the borders-off instrument exemption, the closed-mesh backface proof,
info cards, extract modal, enlarge overlay).

Asserts on the real DOM in a real WebView2, because the parts most likely to
break are the parts pytest cannot see: the overview/view3d tabs actually
LIGHTING for a camera backup (manifest -> BV.tabEnabled -> screens menu), the
robot tabs actually vanishing, view3d's render handing off to BV.cvx3d, the
canvas painting real pixels, and the extract modal's wiring.

Fully synthetic and identifier-clean: the same builder tree the unit tests
use (RB* fixtures, TEST-NET ip), APPDATA redirected BEFORE any backupviewer
import. Run: python tests/ui_cvx3d_probe.py
"""
import json
import sys
import time

from probeutil import check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_cvx3d_probe_")

import webview  # noqa: E402

from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402
from test_cvx_models import build_model_backup  # noqa: E402  (after isolate)

BACKUP = _TMP / "pull"


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.cvx3d_loaded", js(window, "typeof BV.cvx3d === 'object' && typeof BV.cvx3d.render === 'function'"))
        check("boot.meshview_loaded", js(window, "typeof BV.meshView === 'function'"))
        check("boot.viewcube_loaded", js(window, "typeof BV.viewCube === 'function'"))
        check("boot.photofigure_loaded", js(window, "typeof BV.photoFigure === 'function'"))

        # ---- open the synthetic camera backup through the real funnel ----
        js(window, """BV.api.call('open_backup', %s).then(function(m){
            BV.session.open(m); BV.state.setManifest(m);
            location.hash = '#overview';
        })""" % json.dumps(str(BACKUP)))
        sid = poll(window, "BV.state.manifest && BV.state.manifest.sid ? BV.state.manifest.sid : ''")
        check("open.camera_backup", bool(sid), f"(sid={sid!r})")
        check("open.detected_keyence",
              js(window, "BV.state.manifest.backup_type") == "keyence camera")

        # ---- tabs: camera surfaces light, robot surfaces vanish ----
        tabs = js(window, "JSON.stringify(BV.state.manifest.tabs)") or "{}"
        tabs = json.loads(tabs)
        check("tabs.overview_lights", tabs.get("overview") is True)
        check("tabs.view3d_lights", tabs.get("view3d") is True)
        for t in ("registers", "programs", "sysvars", "dcs", "io"):
            check(f"tabs.{t}_dark", not tabs.get(t))
        badges = js(window, """(function(){
            return BV.positionalTabs().map(function(t){ return t.id; });
        })()""") or []
        check("tabs.positional_camera_only",
              all(b in ("overview", "photos", "files") for b in badges), f"({badges})")

        # ---- overview: camera dashboard, robot path untouched ----
        cards = poll(window, """(function(){
            var v = document.getElementById('view');
            if (!v || !v.textContent) return null;
            return v.textContent.indexOf('camera') >= 0 ? v.textContent.slice(0, 2000) : null;
        })()""")
        check("overview.renders", bool(cards))
        if cards:
            check("overview.programs_card", "RB130R01B01CAM1" in cards, "(program name missing)")
            check("overview.3d_card", "3d" in cards)
            # no photos in this fixture: the figure must vanish, not error
            check("overview.no_dead_figure", "image unavailable" not in cards)

        # ---- 3d view: rail, canvas, mesh pixels ----
        js(window, "location.hash = '#view3d'")
        rail = poll(window, """(function(){
            var rows = document.querySelectorAll('.cvx3d-row');
            return rows.length ? [...rows].map(function(r){
                return r.textContent.trim().slice(0, 40); }) : null;
        })()""")
        check("view3d.rail_rows", bool(rail) and len(rail) >= 5, f"({rail})")
        joined = " | ".join(rail or [])
        check("view3d.has_part", "part" in joined.lower(), f"({joined})")
        check("view3d.has_scan", "scan" in joined.lower())
        check("view3d.has_hand", "hand" in joined.lower())
        check("view3d.has_robot", "FANUC" in joined or "robot" in joined.lower())

        # the rail leads with the part models; everything else the backup
        # carries (scans, gripper, arm, calibration, templates) sits behind the
        # toggle - present as evidence, just not in the way
        folded = js(window, """(function(){
            var r = [...document.querySelectorAll('.cvx3d-row')].find(function(x){
                return x.textContent.toLowerCase().indexOf('hand') >= 0; });
            var b = [...document.querySelectorAll('.cvx3d-rail button')].find(function(x){
                return x.textContent.indexOf('other models') >= 0; });
            return JSON.stringify({hidden: !!r && r.offsetParent === null,
                                   toggle: b ? b.textContent : ''});
        })()""")
        folded = json.loads(folded or "{}")
        check("view3d.nonpart_hidden_by_default", folded.get("hidden") is True,
              f"({folded})")
        check("view3d.other_toggle_counts", "(" in (folded.get("toggle") or ""),
              f"({folded.get('toggle')!r})")
        js(window, """[...document.querySelectorAll('.cvx3d-rail button')]
            .find(function(x){ return x.textContent.indexOf('other models') >= 0; }).click()""")
        check("view3d.toggle_reveals", bool(poll(window, """(function(){
            var r = [...document.querySelectorAll('.cvx3d-row')].find(function(x){
                return x.textContent.toLowerCase().indexOf('hand') >= 0; });
            return r && r.offsetParent !== null ? 'y' : null;
        })()""")))

        # first viewable row selected by default -> canvas painted
        painted = poll(window, """(function(){
            var c = document.querySelector('.cvx3d-main canvas');
            if (!c || !c.width) return null;
            var g = c.getContext('2d');
            var d = g.getImageData(0, 0, c.width, c.height).data;
            for (var i = 3; i < d.length; i += 4) if (d[i] > 0) return 'y';
            return null;
        })()""", tries=32)
        check("view3d.canvas_painted", painted == "y")
        head = js(window, "(document.querySelector('.cvx3d-head')||{}).textContent || ''")
        check("view3d.head_counts_triangles", "triangle" in head, f"({head!r})")

        # ---- orientation cube: present, projected, snaps the camera ----
        faces = js(window, "document.querySelectorAll('.cvx3d-main .viewcube .v3-cube-face').length")
        check("cube.faces_drawn", isinstance(faces, int) and 1 <= faces <= 3,
              f"(got {faces} - a cube shows 1-3 faces from any angle)")

        # the borders-off setting must never blank the viewport instruments:
        # sample the cube face + a grid line in BOTH modes (the invariant is
        # "visible either way", not any particular color)
        def opaque(v):
            v = (v or "").replace(" ", "")
            return v not in ("", "none") and not v.endswith(("/0)", ",0)"))
        strokes = js(window, """(function(){
            var face = document.querySelector('.viewcube .v3-cube-face');
            var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('class', 'v3-grid');
            document.querySelector('.viewcube').appendChild(line);
            var out = {};
            ['off', 'on'].forEach(function (mode) {
                document.documentElement.classList.toggle('no-edges', mode === 'on');
                out['face_' + mode] = face ? getComputedStyle(face).stroke : '';
                out['grid_' + mode] = getComputedStyle(line).stroke;
            });
            document.documentElement.classList.remove('no-edges');
            line.remove();
            return JSON.stringify(out);
        })()""") or "{}"
        strokes = json.loads(strokes)
        for k in ("face_off", "face_on", "grid_off", "grid_on"):
            check(f"borders.{k}_visible", opaque(strokes.get(k)), f"({strokes.get(k)!r})")

        snapped = js(window, """(function(){
            var f = [...document.querySelectorAll('.cvx3d-main .viewcube [data-az]')]
                .find(function(x){ return x.getAttribute('data-el') === '90'; });
            if (!f) return '';
            f.dispatchEvent(new MouseEvent('click', {bubbles: true}));
            var st = BV.tabState('view3d');
            return st.az + '/' + st.el;
        })()""")
        check("cube.snap_top", snapped == "180/90", f"({snapped!r})")

        # ---- perspective: off by default, toggles, canvas still paints ----
        pstate = js(window, """(function(){
            var b = [...document.querySelectorAll('.cvx3d-head button')]
                .find(function(x){ return x.textContent === 'perspective'; });
            if (!b) return '';
            var before = b.classList.contains('primary') ? 'on' : 'off';
            b.click();
            var after = b.classList.contains('primary') ? 'on' : 'off';
            return before + '>' + after;
        })()""")
        check("persp.toggles_on", pstate == "off>on", f"({pstate!r})")
        painted2 = poll(window, """(function(){
            var c = document.querySelector('.cvx3d-main canvas');
            if (!c || !c.width) return null;
            var g = c.getContext('2d');
            var d = g.getImageData(0, 0, c.width, c.height).data;
            for (var i = 3; i < d.length; i += 4) if (d[i] > 0) return 'y';
            return null;
        })()""")
        check("persp.canvas_painted", painted2 == "y")
        js(window, """(function(){
            var b = [...document.querySelectorAll('.cvx3d-head button')]
                .find(function(x){ return x.textContent === 'perspective'; });
            if (b && b.classList.contains('primary')) b.click();
        })()""")

        # ---- the closed-mesh proof, on a scratch view with hand-made meshes:
        # a tetrahedron is closed + outward -> some faces cull, the picture
        # keeps the rest; an open sheet must NEVER cull (holes) ----
        culls = js(window, """(function(){
            var d = document.createElement('div');
            d.style.cssText = 'position:absolute;left:-9999px;width:120px;height:120px';
            document.body.appendChild(d);
            var v = BV.meshView(d, {interactive: false});
            var out = {};
            v.setMesh({vertices: [1,1,1, 1,-1,-1, -1,1,-1, -1,-1,1],
                       triangles: [0,1,2, 0,3,1, 0,2,3, 1,3,2],
                       bounds: {min: [-1,-1,-1], max: [1,1,1]}});
            out.tetra = {closed: v._stats.closed, drawn: v._stats.drawn,
                         culled: v._stats.culled};
            v.setMesh({vertices: [0,0,0, 1,0,0, 0,1,0, 1,1,0],
                       triangles: [0,1,2, 1,3,2],
                       bounds: {min: [0,0,0], max: [1,1,0]}});
            out.sheet = {closed: v._stats.closed, drawn: v._stats.drawn,
                         culled: v._stats.culled};
            v.destroy();
            d.remove();
            return JSON.stringify(out);
        })()""") or "{}"
        culls = json.loads(culls)
        tetra, sheet = culls.get("tetra", {}), culls.get("sheet", {})
        check("cull.tetra_proven_closed", tetra.get("closed") is True, f"({tetra})")
        check("cull.tetra_culls_backfaces",
              tetra.get("culled", 0) >= 1 and
              tetra.get("drawn", 0) + tetra.get("culled", 0) == 4, f"({tetra})")
        check("cull.open_sheet_never_culls",
              sheet.get("closed") is False and sheet.get("culled") == 0
              and sheet.get("drawn") == 2, f"({sheet})")

        # ---- info card for a non-viewable entry (the robot) ----
        picked = js(window, """(function(){
            var r = [...document.querySelectorAll('.cvx3d-row')].find(function(x){
                return x.textContent.indexOf('FANUC') >= 0 || x.textContent.toLowerCase().indexOf('robot') >= 0; });
            if (!r) return '';
            r.click();
            return 'y';
        })()""")
        if picked == "y":
            info = poll(window, """(function(){
                var m = document.querySelector('.cvx3d-main');
                return m && m.textContent.indexOf('M-20iD/35') >= 0 ? 'y' : null;
            })()""")
            check("view3d.robot_info_card", info == "y")

        # ---- extract modal opens, lists an .stl, cancels clean ----
        js(window, """(function(){
            var r = [...document.querySelectorAll('.cvx3d-row')].find(function(x){
                return x.textContent.toLowerCase().indexOf('part') >= 0; });
            if (r) r.click();
        })()""")
        time.sleep(0.4)
        opened = js(window, """(function(){
            var b = [...document.querySelectorAll('.cvx3d-head button, #toolbar button')]
                .find(function(x){ return x.textContent.indexOf('extract') >= 0; });
            if (!b) return '';
            b.click();
            return 'y';
        })()""")
        check("extract.button_exists", opened == "y")
        if opened == "y":
            listing = poll(window, """(function(){
                var m = document.querySelector('#modal-root .modal');
                return m && m.textContent.indexOf('.stl') >= 0 ? m.textContent.slice(0, 400) : null;
            })()""")
            check("extract.modal_lists_stl", bool(listing), f"({listing!r})")
            js(window, """(function(){
                var b = [...document.querySelectorAll('#modal-root .modal button')]
                    .find(function(x){ return x.textContent.trim() === 'cancel'; });
                if (b) b.click();
            })()""")
            time.sleep(0.3)
            check("extract.cancel_closes",
                  js(window, "!document.querySelector('#modal-root .modal')"))

        # ---- enlarge overlay opens and closes ----
        big = js(window, """(function(){
            var b = [...document.querySelectorAll('.cvx3d-head button')]
                .find(function(x){ return x.textContent.indexOf('enlarge') >= 0; });
            if (!b) return '';
            b.click();
            return 'y';
        })()""")
        check("enlarge.button_exists", big == "y")
        if big == "y":
            shown = poll(window, """(function(){
                var o = document.querySelector('.meshview-fsov');
                return o && o.querySelector('canvas') ? 'y' : null;
            })()""")
            check("enlarge.overlay_with_canvas", shown == "y")
            check("enlarge.carries_the_cube",
                  js(window, "!!document.querySelector('.meshview-fsov .viewcube')"))
            js(window, """(function(){
                var b = document.querySelector('.meshview-fsov button');
                var all = [...document.querySelectorAll('.meshview-fsov button')];
                var x = all.find(function(k){ return k.textContent.indexOf('\\u2715') >= 0
                    || k.textContent.trim() === 'x' || k.title === 'close'; }) || all[0];
                if (x) x.click();
            })()""")
            time.sleep(0.3)
            check("enlarge.closes",
                  js(window, "!document.querySelector('.meshview-fsov')"))
    finally:
        report()
        window.destroy()


def main():
    build_model_backup(BACKUP)
    api = Api()
    window = webview.create_window(
        "probe",
        url=str(resource_path("web/index.html")),
        js_api=api,
        width=1280,
        height=860,
        hidden=True,
    )
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
