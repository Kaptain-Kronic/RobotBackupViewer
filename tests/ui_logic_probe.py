"""Scratchpad hidden-window probe for the new "logic" tab (tabs/logic.js).

Not committed - it lives in the scratchpad because tests/ is outside this
task's ownership. Boots the real index.html in a hidden WebView2 against the
same synthetic CV-X pull tests/test_cvx_program.py builds (RB* label,
identifier-clean), routes to #logic and asserts on real DOM.

Run: python <this file>
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from probeutil import check, exit_code, isolate, js, poll, report  # noqa: E402

_TMP = isolate("bv_logic_probe_")

import webview  # noqa: E402

from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402
from test_cvx_program import (  # noqa: E402  (after isolate)
    PROGRAM_NAME, SCRIPT_A, SCRIPT_B, container, logic_block,
)

BACKUP = _TMP / "pull"


def build(root):
    """The logic_backup fixture's tree, as a plain function."""
    cam = root / PROGRAM_NAME / "SD1" / "cv-x" / "setting"
    for n in ("001", "002", "003"):
        (cam / n).mkdir(parents=True)
    (cam / "001" / "inspect.dat").write_bytes(container(logic_block(
        [b"Bin Pick Height", b"Color Detection", b"Color Detection",
         b"Edge Pitch", b""],
        [SCRIPT_A, SCRIPT_B])))
    (cam / "002" / "inspect.dat").write_bytes(container(logic_block(
        [b"Edge Pitch"], [SCRIPT_B])))
    (cam / "003" / "inspect.dat").write_bytes(b"not an ST container" + bytes(64))
    return root


def probe(window):
    try:
        time.sleep(4)  # boot

        check("boot.tab_registered",
              js(window, "BV.tabs.some(function(t){return t.id==='logic';})"))
        check("boot.tab_label",
              js(window, "(BV.tabs.filter(function(t){return t.id==='logic';})[0]||{}).label")
              == "logic")

        js(window, """BV.api.call('open_backup', %s).then(function(m){
            BV.session.open(m); BV.state.setManifest(m);
            location.hash = '#logic';
        })""" % repr(str(BACKUP)).replace("'", '"'))
        sid = poll(window, "BV.state.manifest && BV.state.manifest.sid ? BV.state.manifest.sid : ''")
        check("open.backup", bool(sid), f"(sid={sid!r})")
        check("open.logic_tab_lights", js(window, "!!BV.state.manifest.tabs.logic"))
        check("open.tab_has_a_badge",
              "logic" in (js(window, "BV.positionalTabs().map(function(t){return t.id;})") or []),
              f"({js(window, 'BV.positionalTabs().map(function(t){return t.id;})')})")

        # ---- the rail: two programs, three scripts ----
        rows = poll(window, "document.querySelectorAll('.logic-row').length")
        check("rail.rows", rows == 3, f"(got {rows})")
        check("rail.groups_when_two_programs",
              js(window, "document.querySelectorAll('.logic-group').length") == 2)
        titles = js(window, """[].map.call(document.querySelectorAll('.logic-row'),
            function(r){ return r.textContent.trim(); })""")
        check("rail.first_row_is_the_titled_script",
              "Bin Pick Height Check" in titles[0] and "8 lines" in titles[0], f"({titles[0]!r})")
        check("rail.untitled_says_so",
              "untitled" in titles[1] and "3 lines" in titles[1], f"({titles[1]!r})")

        # ---- the code pane: the longest script, line for line ----
        lines = js(window, """[].map.call(
            document.querySelectorAll('.logic-code .code-line .lc'),
            function(n){ return n.textContent; })""") or []
        check("code.line_count", len(lines) == len(SCRIPT_A), f"(got {len(lines)})")
        check("code.line_for_line", lines == SCRIPT_A, f"({lines!r})")
        check("code.is_monospace", "mono" in (js(window, """(function(){
            var p = document.querySelector('.logic-code pre');
            return p ? getComputedStyle(p).fontFamily : '';
        })()""") or "").lower() or "consolas" in (js(window, """(function(){
            var p = document.querySelector('.logic-code pre');
            return p ? getComputedStyle(p).fontFamily : '';
        })()""") or "").lower())
        check("code.preserves_whitespace",
              js(window, """(function(){
                  var n = document.querySelector('.logic-code .code-line .lc');
                  return n ? getComputedStyle(n).whiteSpace : '';
              })()""") in ("pre-wrap", "break-spaces"))
        check("code.pane_scrolls_on_its_own",
              js(window, """(function(){
                  var n = document.querySelector('.logic-code');
                  return n ? getComputedStyle(n).overflowY : '';
              })()""") in ("auto", "scroll"))

        # ---- the two tints, and ONLY those two ----
        tint = js(window, """(function(){
            var out = {comment: 0, ref: 0, other: 0};
            var sub = getComputedStyle(document.body).getPropertyValue('--sub').trim();
            var acc = getComputedStyle(document.body).getPropertyValue('--accent').trim();
            [].forEach.call(document.querySelectorAll('.logic-code .lc span'), function(s){
                var st = (s.getAttribute('style') || '');
                if (st.indexOf('--sub') >= 0) out.comment++;
                else if (st.indexOf('--accent') >= 0) out.ref++;
                else out.other++;
            });
            out.sub = sub; out.acc = acc;
            return JSON.stringify(out);
        })()""")
        check("tint.comment_line", '"comment":1' in tint, f"({tint})")
        # SCRIPT_A carries two Tnnn.RSLT refs on its two @local lines
        check("tint.refs", '"ref":2' in tint, f"({tint})")
        check("tint.nothing_else_tinted", '"other":0' in tint, f"({tint})")

        # ---- names + the honesty sentence ----
        names = js(window, """(function(){
            var n = document.querySelector('.logic-names');
            return n ? n.textContent : '';
        })()""") or ""
        for want in ("Bin Pick Height", "Color Detection", "Edge Pitch"):
            check(f"names.lists_{want.split()[0].lower()}", want in names)
        check("names.count_shown", "×2" in names, f"({names[:200]!r})")
        check("names.honesty_sentence",
              "built-in tool-type names" in names and
              "nothing in the file says which tool" in names,
              f"({names[-220:]!r})")

        # ---- toolbar copy button ----
        copy = js(window, """(function(){
            var b = [].filter.call(document.querySelectorAll('#toolbar button'),
                function(x){ return x.textContent.indexOf('copy') >= 0; })[0];
            return b ? (b.disabled ? 'disabled' : 'enabled') : 'missing';
        })()""")
        check("toolbar.copy_button", copy == "enabled", f"({copy})")

        # ---- selecting another script repaints code + names ----
        js(window, "document.querySelectorAll('.logic-row')[2].click()")
        time.sleep(0.4)
        lines2 = js(window, """[].map.call(
            document.querySelectorAll('.logic-code .code-line .lc'),
            function(n){ return n.textContent; })""") or []
        check("select.switches_script", lines2 == SCRIPT_B, f"({lines2!r})")
        names2 = js(window, "document.querySelector('.logic-names').textContent") or ""
        check("select.names_follow_the_program",
              "Edge Pitch" in names2 and "Bin Pick Height" not in names2,
              f"({names2[:160]!r})")
        check("select.state_remembered",
              js(window, "JSON.stringify(BV.tabState('logic').sel)")
              == '{"rel":"%s/SD1/cv-x/setting/002/inspect.dat","i":0}' % PROGRAM_NAME,
              f"({js(window, 'JSON.stringify(BV.tabState(\"logic\").sel)')})")

        # ---- leave and come back: the selection is restored ----
        js(window, "location.hash = '#files'")
        time.sleep(0.5)
        js(window, "location.hash = '#logic'")
        time.sleep(0.8)
        lines3 = js(window, """[].map.call(
            document.querySelectorAll('.logic-code .code-line .lc'),
            function(n){ return n.textContent; })""") or []
        check("restore.same_script", lines3 == SCRIPT_B, f"({lines3!r})")
        check("restore.row_highlighted",
              js(window, """(function(){
                  var rs = document.querySelectorAll('.logic-row');
                  return [].filter.call(rs, function(r){
                      return (r.style.boxShadow || '').length > 0; }).length;
              })()""") == 1)
        check("restore.scroll_keys_wired",
              js(window, """(function(){
                  var a = document.querySelector('.logic-rail');
                  var b = document.querySelector('.logic-code');
                  return (a && a._bvScrollKey) + '|' + (b && b._bvScrollKey);
              })()""") == "logic-rail|logic-code")

        # ---- a robot backup never reaches this screen ----
        rb = _TMP / "rb"
        rb.mkdir()
        (rb / "SUMMARY.DG").write_text("F Number: F999999\n", encoding="cp1252")
        js(window, """BV.api.call('open_backup', %s).then(function(m){
            BV.session.open(m); BV.state.setManifest(m);
        })""" % repr(str(rb)).replace("'", '"'))
        time.sleep(1.5)
        check("robot.tab_dark", not js(window, "!!BV.state.manifest.tabs.logic"))
    finally:
        report()
        window.destroy()


def main():
    build(BACKUP)
    api = Api()
    window = webview.create_window(
        "logic probe",
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
