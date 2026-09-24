"""Hidden-window probe for the position display: the registers tab's PR
table and the program detail's positions card.

What it pins (the design, not incidental numbers):
- the group number is not a column - a multi-group register is ONE row whose
  groups are pages of its expanded card (‹ › at the bottom of the data)
- a row expands in place into every axis of the group at full precision,
  extended axes (a rail's E1) included, and the windowed table's row math
  stays exact around the taller row
- expansion and the chosen page survive leaving the tab and coming back
- enter toggles the selected row, like a click
- the program detail's static positions table does the same with .tbl-detail
  rows, one row per taught point (never per group)

Fully synthetic and identifier-clean: an RB fake in a temp folder, APPDATA
redirected before any backupviewer import.
Run: python tests/ui_positions_probe.py
"""
import json
import sys
import time

from probeutil import FAILURES, check, exit_code, isolate, js, poll, report

_TMP = isolate("bv_positions_probe_")

import webview  # noqa: E402

from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

BACKUP = _TMP / "RB010R01B01"

# one rail robot with a second motion group: PR[7] is taught for both groups,
# PR[6] carries the rail axis, PR[2] is named but untaught (kept by hide-empty)
POSREG = (
    "[*POSREG*]$POSREG  Storage: CMOS  Access: RW  : ARRAY[2,8] OF Position Reg\n"
    "    [1,1] =   'Home1'   Group: 1\n"
    "  J1 =     0.000 deg   J2 =   -60.000 deg   J3 =     0.000 deg \n"
    "  J4 =     0.000 deg   J5 =    90.000 deg   J6 =      .000 deg \n"
    "  EXT1:     0.000 mm  \n"
    "\n"
    "    [1,2] =   'Home2' Uninitialized\n"
    "    [1,3] =   '' Uninitialized\n"
    "    [1,4] =   '' Uninitialized\n"
    "    [1,5] =   '' Uninitialized\n"
    "    [1,6] =   'Maintenance' \n"
    "  Group: 1   Config: F U T, 0, 0, 0\n"
    "  X:  2066.881   Y: -1269.113   Z: -1122.291\n"
    "  W:  -179.786   P:     -.649   R:   -58.819\n"
    "  EXT1:    40.492 mm  \n"
    "    [1,7] =   'Turn' \n"
    "  Group: 1   Config: N U T, 0, 0, 0\n"
    "  X:  1600.000   Y: -1000.000   Z:  -850.000\n"
    "  W:  -180.000   P:     0.000   R:  -180.000\n"
    "    [1,8] =   '' Uninitialized\n"
    "    [2,1] =   'Home1' Uninitialized\n"
    "    [2,2] =   'Home2' Uninitialized\n"
    "    [2,3] =   '' Uninitialized\n"
    "    [2,4] =   '' Uninitialized\n"
    "    [2,5] =   '' Uninitialized\n"
    "    [2,6] =   'Maintenance' Uninitialized\n"
    "    [2,7] =   'Turn'   Group: 2\n"
    "  J1 =    45.000 deg \n"
    "    [2,8] =   '' Uninitialized\n"
)

LS = (
    "/PROG  RAIL\n/ATTR\n/MN\n   1:J P[1] 100% FINE ;\n   2:J P[2] 100% FINE ;\n"
    "   3:J P[3] 100% FINE ;\n/POS\n"
    'P[1:"To Infd"]{\n   GP1:\n'
    "\tUF : 1, UT : 1,\t\tCONFIG : 'F U T, 1, 0, 0',\n"
    "\tX = -1632.357  mm,\tY =   358.358  mm,\tZ =    90.419  mm,\n"
    "\tW =  -178.180 deg,\tP =     -.063 deg,\tR =   131.632 deg,\n"
    "\tE1=   900.000  mm\n};\n"
    "P[2]{\n   GP1:\n\tUF : 0, UT : 1,\t\n"
    "\tJ1=     0.000 deg,\tJ2=   -60.000 deg,\tJ3=     0.000 deg,\n"
    "\tJ4=     0.000 deg,\tJ5=    90.000 deg,\tJ6=      .000 deg,\n"
    "\tE1=  ********  mm\n"
    "   GP2:\n\tUF : 0, UT : 1,\t\n\tJ1=    45.000 deg\n};\n"
    "P[3]{\n   GP1:\n\tUF : 0, UT : 1,\t\n"
    "\tJ1=    10.000 deg,\tJ2=   -30.000 deg,\tJ3=     5.000 deg,\n"
    "\tJ4=     0.000 deg,\tJ5=   -60.000 deg,\tJ6=     0.000 deg\n};\n"
    "/END\n"
)


def build():
    BACKUP.mkdir(parents=True)
    (BACKUP / "SUMMARY.DG").write_text("Robot: RB010R01B01\n", encoding="cp1252")
    (BACKUP / "POSREG.VA").write_text(POSREG, encoding="cp1252", newline="\r\n")
    (BACKUP / "RAIL.LS").write_text(LS, encoding="cp1252", newline="\r\n")


# a VTable row whose text carries `needle` (registers split view: one list
# over two panes, so the row exists in exactly one of them)
def row_js(needle):
    return ("[].slice.call(document.querySelectorAll('.vt-row')).find(function(r){"
            "return r.textContent.indexOf(%s)>=0;})" % json.dumps(needle))


def key(window, k):
    js(window, "document.dispatchEvent(new KeyboardEvent('keydown',"
               "{key:%s, bubbles:true, cancelable:true}))" % json.dumps(k))


def probe(window):
    try:
        time.sleep(4)  # boot
        js(window, """BV.api.call('open_backup', %s).then(function(m){
            BV.session.open(m); BV.state.setManifest(m);
            location.hash = '#registers/pos';
        })""" % json.dumps(str(BACKUP)))
        sid = poll(window, "BV.state.manifest && BV.state.manifest.sid ? BV.state.manifest.sid : ''")
        check("open.backup", bool(sid), f"(sid={sid!r})")
        nrows = poll(window, "document.querySelectorAll('.vt-row').length")
        check("pr.rows_render", (nrows or 0) > 0, f"({nrows})")

        # ---- the group number is not a column ----
        heads = js(window, """[].slice.call(document.querySelectorAll(
            '.mt-pane:first-child .vt-head .vt-label')).map(function(l){
            return l.textContent.replace(/[▲▼]/g,'').trim();})""") or []
        check("pr.no_group_column", heads == ["#", "name", "value"], f"({heads})")

        # ---- one row per register, groups folded; hide-empty is honest ----
        all_rows = js(window, """[].slice.call(document.querySelectorAll('.vt-row'))
            .map(function(r){return r.textContent;})""") or []
        pr7 = [t for t in all_rows if "PR[7]" in t]
        check("pr.two_groups_one_row", len(pr7) == 1, f"({len(pr7)} rows)")
        check("pr.multi_group_marker", pr7 and "2 grp" in pr7[0], f"({pr7[0][:60] if pr7 else ''!r})")
        pr6 = [t for t in all_rows if "PR[6]" in t]
        check("pr.summary_shows_rail", pr6 and "e1" in pr6[0] and "40.5" in pr6[0],
              f"({pr6[0][:80] if pr6 else ''!r})")
        # named-but-untaught stays listed; unnamed untaught hides
        check("pr.hide_empty_keeps_named", any("PR[2]" in t for t in all_rows))
        check("pr.hide_empty_drops_unnamed", not any("PR[3]" in t for t in all_rows))

        # ---- click expands in place: every axis, full precision, E1 ----
        js(window, row_js("PR[6]") + ".click()")
        card = poll(window, "document.querySelector('.vt-row.expanded .pos-card') ? 'y' : ''")
        check("pr.click_expands", card == "y")
        axes = js(window, """[].slice.call(document.querySelectorAll(
            '.vt-row.expanded .pos-ax')).map(function(a){return a.textContent;})""") or []
        check("pr.card_lists_every_axis", len(axes) == 7, f"({axes})")
        check("pr.card_full_precision", any("2066.881" in a for a in axes), f"({axes})")
        check("pr.card_shows_e1_with_unit", any(a.startswith("E1") and "40.492" in a and "mm" in a
                                                for a in axes), f"({axes})")
        check("pr.card_head_has_rep_and_config",
              js(window, "(document.querySelector('.vt-row.expanded .pos-head')||{}).textContent") is not None
              and "cartesian" in (js(window, "document.querySelector('.vt-row.expanded .pos-head').textContent") or "")
              and "F U T" in (js(window, "document.querySelector('.vt-row.expanded .pos-head').textContent") or ""))
        # taught for group 1 only: no pager, no pill - the untaught group is
        # named once under the data instead
        check("pr.single_group_has_no_pager",
              not js(window, "!!document.querySelector('.vt-row.expanded .pos-pager')"))
        check("pr.untaught_group_named_not_paged",
              (js(window, "(document.querySelector('.vt-row.expanded .pos-untaught')||{}).textContent") or "")
              == "gp 2 untaught")
        check("pr.taught_once_has_no_marker", pr6 and "grp" not in pr6[0], f"({pr6[0][:40] if pr6 else ''!r})")

        # ---- the windowed math stays exact around the taller row ----
        geom = js(window, """(function(){
            var pane = document.querySelector('.vt-row.expanded').closest('.vtable');
            var rows = [].slice.call(pane.querySelectorAll('.vt-row')).sort(function(a,b){
                return parseFloat(a.style.top)-parseFloat(b.style.top);});
            var i = rows.findIndex(function(r){return r.classList.contains('expanded');});
            var ex = rows[i], nx = rows[i+1], pv = rows[i-1];
            var base = pv ? parseFloat(pv.style.height) : parseFloat(nx.style.height);
            var detail = ex.querySelector('.vt-detail').offsetHeight;
            return JSON.stringify({exH: parseFloat(ex.style.height), base: base, detail: detail,
                next: nx ? parseFloat(nx.style.top) - parseFloat(ex.style.top) : null,
                spacer: parseFloat(pane.querySelector('.vt-spacer').style.height),
                n: pane.querySelectorAll('.vt-row').length});
        })()""")
        g = json.loads(geom or "{}")
        check("pr.expanded_row_is_base_plus_detail",
              g and abs(g["exH"] - (g["base"] + g["detail"])) < 1 and g["detail"] > g["base"], f"({g})")
        check("pr.next_row_starts_after_it", g and g["next"] is not None and abs(g["next"] - g["exH"]) < 1, f"({g})")
        check("pr.spacer_grew_by_the_detail",
              g and abs(g["spacer"] - (g["n"] * g["base"] + g["detail"])) < 1, f"({g})")

        # ---- a multi-group register pages through its groups ----
        js(window, row_js("PR[7]") + ".click()")
        pager = poll(window, """(function(){var r=%s; var p=r&&r.querySelector('.pos-pager .pos-page');
            return p?p.textContent:'';})()""" % row_js("PR[7]"))
        check("pr.pager_present", "group 1" in (pager or "") and "1/2" in (pager or ""), f"({pager!r})")
        g1axes = js(window, "[].slice.call(%s.querySelectorAll('.pos-ax')).length" % row_js("PR[7]"))
        check("pr.page_one_is_group_one", g1axes == 6, f"({g1axes} axes)")
        js(window, row_js("PR[7]") + ".querySelector('.pos-next').click()")
        pager2 = poll(window, """(function(){var p=%s.querySelector('.pos-pager .pos-page');
            return p && p.textContent.indexOf('group 2')>=0 ? p.textContent : '';})()""" % row_js("PR[7]"))
        check("pr.next_turns_the_page", "group 2" in (pager2 or "") and "2/2" in (pager2 or ""), f"({pager2!r})")
        g2axes = js(window, """[].slice.call(%s.querySelectorAll('.pos-ax')).map(function(a){
            return a.textContent;})""" % row_js("PR[7]")) or []
        check("pr.page_two_is_the_positioner", g2axes == ["J145.000deg"], f"({g2axes})")
        check("pr.next_disabled_on_last_page",
              js(window, row_js("PR[7]") + ".querySelector('.pos-next').disabled") is True)
        check("pr.still_one_row_for_pr7",
              js(window, """[].slice.call(document.querySelectorAll('.vt-row')).filter(function(r){
                  return r.textContent.indexOf('PR[7]')>=0;}).length""") == 1)
        check("pr.two_rows_expanded",
              js(window, "document.querySelectorAll('.vt-row.expanded').length") == 2)

        # ---- leaving and coming back restores the open set and the page ----
        js(window, "location.hash = '#overview'")
        poll(window, "location.hash === '#overview' ? 'y' : ''")
        time.sleep(0.6)
        js(window, "location.hash = '#registers/pos'")
        back = poll(window, "document.querySelectorAll('.vt-row.expanded').length === 2 ? 'y' : ''")
        check("pr.expansion_survives_leave", back == "y",
              f"({js(window, 'document.querySelectorAll(\".vt-row.expanded\").length')} expanded)")
        pager3 = js(window, "(%s.querySelector('.pos-pager .pos-page')||{}).textContent" % row_js("PR[7]")) or ""
        check("pr.page_survives_leave", "group 2" in pager3, f"({pager3!r})")

        # ---- enter toggles the selected row ----
        js(window, "BV.currentVTable.selectWhere(function(r){return r.index===6;})")
        key(window, "Enter")
        collapsed = poll(window, "document.querySelectorAll('.vt-row.expanded').length === 1 ? 'y' : ''")
        check("pr.enter_collapses_selected", collapsed == "y")
        key(window, "Enter")
        reopened = poll(window, "document.querySelectorAll('.vt-row.expanded').length === 2 ? 'y' : ''")
        check("pr.enter_reopens_selected", reopened == "y")

        # ---- the card offers the search the row click used to be ----
        js(window, row_js("PR[6]") + ".querySelector('.pos-act').click()")
        check("pr.find_uses_goes_to_search",
              poll(window, "location.hash.indexOf('#search/') === 0 ? location.hash : ''")
              == "#search/PR%5B6%5D", f"({js(window, 'location.hash')!r})")

        # ---- program detail: one row per point, expandable, E1 shown ----
        js(window, "location.hash = '#programs/RAIL.LS'")
        prow = poll(window, "document.querySelectorAll('#view .card .tbl tbody tr.has-detail').length")
        check("prog.one_row_per_point", prow == 3, f"({prow} rows for 3 points / 4 groups)")
        pheads = js(window, """[].slice.call(document.querySelectorAll(
            '#view .card .tbl thead th')).map(function(t){return t.textContent;})""") or []
        check("prog.no_group_column", pheads == ["p", "uf/ut", "value"], f"({pheads})")
        ptext = js(window, """[].slice.call(document.querySelectorAll('#view .card .tbl tbody tr.has-detail'))
            .map(function(r){return r.textContent;})""") or []
        check("prog.summary_shows_rail", any("P[1]" in t and "e1" in t and "900.0" in t for t in ptext), f"({ptext})")
        check("prog.masked_point_says_so", any("P[2]" in t and "2 grp" in t for t in ptext), f"({ptext})")
        js(window, """[].slice.call(document.querySelectorAll('#view .card .tbl tbody tr.has-detail'))
            .find(function(r){return r.textContent.indexOf('P[1]')>=0;}).click()""")
        paxes = poll(window, """(function(){var d=document.querySelector('#view .card .tbl tr.tbl-detail');
            if(!d) return ''; return JSON.stringify([].slice.call(d.querySelectorAll('.pos-ax'))
            .map(function(a){return a.textContent;}));})()""")
        paxes = json.loads(paxes or "[]")
        check("prog.click_expands_every_axis", len(paxes) == 7 and any("E1" in a and "900.000" in a for a in paxes),
              f"({paxes})")
        check("prog.detail_sits_under_its_row",
              js(window, """(function(){var d=document.querySelector('#view .card .tbl tr.tbl-detail');
                return d.previousElementSibling.textContent.indexOf('P[1]')>=0 &&
                       d.previousElementSibling.classList.contains('open');})()"""))
        js(window, """[].slice.call(document.querySelectorAll('#view .card .tbl tbody tr.has-detail'))
            .find(function(r){return r.textContent.indexOf('P[2]')>=0;}).click()""")
        p2 = poll(window, """(function(){var rows=[].slice.call(document.querySelectorAll(
            '#view .card .tbl tr.tbl-detail')); var d=rows[1]; if(!d) return '';
            return JSON.stringify({masked: !!d.querySelector('.pill.warn'),
                pager: (d.querySelector('.pos-page')||{}).textContent||'',
                e1: [].slice.call(d.querySelectorAll('.pos-ax')).map(function(a){return a.textContent;})
                    .filter(function(t){return t.indexOf('E1')===0;})[0]||''});})()""")
        p2 = json.loads(p2 or "{}")
        check("prog.masked_rail_is_masked_not_zero", p2 and p2["masked"] and "********" in p2["e1"], f"({p2})")
        check("prog.two_groups_page", p2 and "group 1" in p2["pager"] and "1/2" in p2["pager"], f"({p2})")
        js(window, "document.querySelectorAll('#view .card .tbl tr.tbl-detail')[1].querySelector('.pos-next').click()")
        p2b = poll(window, """(function(){var d=document.querySelectorAll('#view .card .tbl tr.tbl-detail')[1];
            var p=d&&d.querySelector('.pos-page'); return p&&p.textContent.indexOf('group 2')>=0?
            JSON.stringify([].slice.call(d.querySelectorAll('.pos-ax')).map(function(a){return a.textContent;})):'';})()""")
        check("prog.page_two_is_the_positioner", json.loads(p2b or "[]") == ["J145.000deg"], f"({p2b})")

        # ---- program open set + page survive leaving too ----
        js(window, "location.hash = '#programs'")
        poll(window, "location.hash === '#programs' ? 'y' : ''")
        time.sleep(0.5)
        js(window, "location.hash = '#programs/RAIL.LS'")
        pback = poll(window, "document.querySelectorAll('#view .card .tbl tr.tbl-detail').length === 2 ? 'y' : ''")
        check("prog.expansion_survives_leave", pback == "y")
        pg = js(window, "(document.querySelectorAll('#view .card .tbl tr.tbl-detail')[1].querySelector('.pos-page')||{}).textContent") or ""
        check("prog.page_survives_leave", "group 2" in pg, f"({pg!r})")
        # collapse again: the detail row goes away, the open set forgets it
        js(window, """[].slice.call(document.querySelectorAll('#view .card .tbl tbody tr.has-detail'))
            .find(function(r){return r.textContent.indexOf('P[1]')>=0;}).click()""")
        check("prog.click_collapses",
              js(window, "document.querySelectorAll('#view .card .tbl tr.tbl-detail').length") == 1)
    finally:
        report()
        window.destroy()


def main():
    build()
    api = Api()
    window = webview.create_window(
        "positions probe",
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
