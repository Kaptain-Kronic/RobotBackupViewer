"""Hidden-window probe for the plant-link indicator (netlink/discover + netstatus.js).

What it pins down:
- the pill renders into its own #status-net slot, and the live reading is a
  member of the declared state vocabulary (membership, never a value - a dev
  box's real link state varies, and freezing one in would make this suite
  machine-dependent);
- every state maps to a DISTINCT pill variant, which is the actual invariant of
  the state->look table;
- the panel opens on click and closes on Escape, through BV.dropPanel;
- the honesty locks, which are the whole point of the feature:
    * an `absent` device (in the library, no neighbour entry) renders hollow and
      is NEVER painted as a fault - a camera nobody has talked to since the
      cable went in is not down;
    * a device that is not in the library is flagged as such rather than hidden;
    * a failed read dims the chip instead of inventing a verdict;
    * a pin to an adapter that is not in the table names itself, shows as the
      choice in force, and offers the way out - it never traps;
- the pill is the app's own chip (no dot, no button chrome, the credit pill's
  size) and every reason the watch can give has words, never a raw slug.

Fully synthetic and identifier-clean: the bridge endpoint is stubbed with canned
TEST-NET payloads, so nothing here reads a real adapter or touches a network.
Run: python tests/ui_netstatus_probe.py
"""
import json
import sys
import time
from probeutil import FAILURES, check, exit_code, isolate, js, poller, report

_TMP = isolate("bv_net_probe_")

import webview  # noqa: E402

from backupviewer import discover  # noqa: E402
from backupviewer import settings as bv_settings  # noqa: E402
from backupviewer.api import Api  # noqa: E402
from backupviewer.app import resource_path  # noqa: E402

poll = poller(tries=20, delay=0.4)

ADAPTER = {"name": "Ethernet 3", "ifindex": 1, "kind": "ethernet", "up": True,
           "ip": "192.0.2.37", "prefix": 24, "gateway": "192.0.2.1",
           "mac": "AA:BB:CC:DD:EE:01", "speed": 1000000000}

DEVICES = [
    {"ip": "192.0.2.1", "mac": "AA:BB:CC:DD:EE:99", "dot": "known", "reach_ms": 0,
     "in_library": False, "name": "", "device_type": "", "line": "",
     "vendor_kind": "", "gateway": True},
    {"ip": "192.0.2.40", "mac": "AA:BB:CC:00:00:01", "dot": "live", "reach_ms": 5,
     "in_library": True, "name": "RB010R01B01", "device_type": "robot",
     "line": "RB", "vendor_kind": "", "gateway": False},
    {"ip": "192.0.2.41", "mac": "AA:BB:CC:00:00:02", "dot": "known", "reach_ms": 0,
     "in_library": True, "name": "RB010R02B01CAM1", "device_type": "camera-mtx",
     "line": "RB", "vendor_kind": "", "gateway": False},
    # the stranger: on the wire, not in the library
    {"ip": "192.0.2.77", "mac": "AA:BB:CC:11:11:11", "dot": "live", "reach_ms": 1,
     "in_library": False, "name": "", "device_type": "", "line": "",
     "vendor_kind": "", "gateway": False},
    # the honesty case: known to the library, never spoken to -> hollow, not red
    {"ip": "192.0.2.50", "mac": "", "dot": "absent", "reach_ms": 0,
     "in_library": True, "name": "RC020R01B01", "device_type": "robot",
     "line": "RC", "vendor_kind": "", "gateway": False},
]


def payload(**over):
    p = {
        "state": "ok", "detail": "gateway 192.0.2.1 answering", "since_ms": 4000,
        "probe_ok": True, "why": "library", "adapter": ADAPTER,
        "cidr": "192.0.2.0/24", "gateway": "192.0.2.1",
        "states": ["unknown", "no-adapter", "no-link", "no-ip", "no-gateway", "ok"],
        "seen": 4, "checking": False, "devices": DEVICES, "pin": None,
        "adapters": [
            {"name": "Ethernet 3", "mac": "AA:BB:CC:DD:EE:01", "kind": "ethernet",
             "up": True, "ip": "192.0.2.37", "cidr": "192.0.2.0/24",
             "library": 12, "neighbours": 4},
            {"name": "Wi-Fi", "mac": "AA:BB:CC:DD:EE:02", "kind": "wifi",
             "up": True, "ip": "198.51.100.10", "cidr": "198.51.100.0/24",
             "library": 0, "neighbours": 1},
        ],
    }
    p.update(over)
    return p


def stub(window, p):
    """Canned bridge answer, and the same payload kept on window so a repaint
    can be driven by hand (this window is hidden, so the poller never ticks)."""
    js(window, """window.__probe_payload = %s;
    window.pywebview.api.net_status = function () {
        return Promise.resolve({ ok: true, data: window.__probe_payload });
    };""" % json.dumps(p))


def probe(window):
    try:
        time.sleep(3)

        check("boot.module", js(window, "!!BV.netstatus"))
        check("boot.slot_is_its_own_span",
              js(window, "!!document.getElementById('status-net')"))
        pill = poll(window, "(document.querySelector('#status-net .net-pill')||{}).textContent||''")
        check("boot.pill_rendered", bool(pill), f"(got {pill!r})")

        # the live reading must be one of the declared states - assert
        # membership, never a value: this machine's link is whatever it is
        labels = js(window, """JSON.stringify(BV.netstatus.STATES
            .map(function (s) { return BV.netstatus._look(s)[0]; }))""")
        labels = json.loads(labels or "[]")
        word = (pill or "").split(" · ")[0]
        check("boot.state_is_declared", word in labels, f"(got {word!r} of {labels})")

        # every state gets its OWN variant - that table is the invariant
        variants = js(window, """(function () {
            var seen = {};
            BV.netstatus.STATES.forEach(function (s) {
                BV.netstatus._render({ state: s, detail: '', since_ms: 0,
                                       probe_ok: true, adapter: {} });
                seen[s] = document.querySelector('#status-net .net-pill').className;
            });
            return JSON.stringify(seen);
        })()""")
        variants = json.loads(variants or "{}")
        check("look.every_state_rendered", len(variants) == len(labels), f"({variants})")
        # distinct WORDS, not distinct variants: `no ip` and `no link` share the
        # error colour on purpose (both mean "this end is the problem"), so the
        # words are what has to tell them apart
        check("look.every_state_reads_differently",
              len(set(labels)) == len(labels), f"({labels})")
        check("look.variants_are_known_pills",
              all(v.split()[1] in ("ok-soft", "warn", "err", "off", "ghost", "acc", "on")
                  for v in variants.values()), f"({variants})")

        # the pill is the statusbar's own chip: nothing inside it (the dot is
        # gone), and the credit pill's size, radius and padding - a chip that
        # draws differently from its siblings is the thing this replaced
        same = json.loads(js(window, """(function () {
            var n = document.querySelector('#status-net .net-pill');
            var c = document.querySelector('#statusbar .credit-pill');
            if (!n || !c) return 'null';
            var a = getComputedStyle(n), b = getComputedStyle(c);
            return JSON.stringify({ kids: n.children.length,
                fs: a.fontSize === b.fontSize, radius: a.borderRadius === b.borderRadius,
                pad: a.paddingTop === b.paddingTop && a.paddingLeft === b.paddingLeft });
        })()""") or "null")
        check("look.pill_has_nothing_inside", bool(same) and same.get("kids") == 0, f"({same})")
        check("look.pill_draws_like_its_siblings",
              bool(same) and same.get("fs") and same.get("radius") and same.get("pad"),
              f"({same})")

        # every reason the watch can give has words: the two tables are held
        # equal at the seam, so a new `why` in python cannot reach the "chosen"
        # row as a raw slug (pinned-missing did, for weeks)
        whys = json.loads(js(window, "JSON.stringify(BV.netstatus.WHYS)") or "[]")
        check("why.tables_match_at_the_seam", set(whys) == set(discover.LINK_WHYS),
              f"(js {sorted(whys)} vs py {sorted(discover.LINK_WHYS)})")
        words = json.loads(js(window, """JSON.stringify(BV.netstatus.WHYS.map(
            function (w) { return BV.netstatus._why(w); }))""") or "[]")
        check("why.no_raw_slugs", bool(words) and all(t and t != w for w, t in zip(whys, words)),
              f"({dict(zip(whys, words))})")

        # an unverified reading is dimmed, not recoloured into a verdict
        js(window, """BV.netstatus._render({ state: 'ok', detail: '', since_ms: 0,
                                             probe_ok: false, adapter: {} })""")
        check("look.unread_is_dimmed",
              "unread" in (js(window,
                  "document.querySelector('#status-net .net-pill').className") or ""))

        # ---- the panel ----
        stub(window, payload())
        js(window, "BV.netstatus.tick(true)")
        time.sleep(0.6)
        js(window, "document.querySelector('#status-net .net-pill').click()")
        opened = poll(window, "!!document.querySelector('.bv-drop .net-panel')")
        check("panel.opens", bool(opened))

        # THE placement lock. This panel hangs off the statusbar at the very
        # bottom of the window, so if it is ever placed while empty, dropPanel
        # measures an empty box (14px), tucks it just above the anchor, and the
        # panel then grows 440px PAST the bottom of the screen - 8% of it
        # visible. Assert it is fully on screen and actually uses the room.
        geo = json.loads(js(window, """(function () {
            var d = document.querySelector('.bv-drop');
            var b = d.getBoundingClientRect();
            var sb = document.getElementById('statusbar').getBoundingClientRect();
            return JSON.stringify({
                top: Math.round(b.top), bottom: Math.round(b.bottom),
                h: Math.round(b.height), vh: window.innerHeight,
                room: Math.round(sb.top)
            });
        })()""") or "{}")
        check("panel.not_below_the_viewport", geo.get("bottom", 1e9) <= geo.get("vh", 0),
              f"(bottom {geo.get('bottom')} vs viewport {geo.get('vh')})")
        check("panel.not_clipped_above", geo.get("top", -1) >= 0, f"({geo})")
        # it must earn its space: more than half the height available above the
        # statusbar, or it has silently collapsed again
        check("panel.uses_the_room",
              geo.get("h", 0) > geo.get("room", 0) * 0.5,
              f"(height {geo.get('h')} of {geo.get('room')} available)")
        # pinned, not floated: it lives in the bottom slab so its position can
        # never go stale, which is why scrolling the library behind it must NOT
        # dismiss it - you read link dots while you work
        check("panel.is_pinned_to_the_slab", js(window, """(function () {
            var d = document.querySelector('.bv-drop');
            return !!d && !!d.closest('#chrome-bottom');
        })()"""))
        js(window, "window.dispatchEvent(new Event('scroll', {bubbles:true}))")
        time.sleep(0.3)
        check("panel.survives_a_page_scroll",
              js(window, "!!document.querySelector('.bv-drop .net-panel')"))

        # "all of the connection info" - the box must answer the whole question
        # without sending anyone to ipconfig, and every value is something the OS
        # told us rather than something we inferred
        facts = js(window, """(function () {
            var o = {};
            document.querySelectorAll('.bv-drop .net-fact').forEach(function (r) {
                o[r.children[0].textContent] = r.children[1].textContent;
            });
            return JSON.stringify(o);
        })()""")
        facts = json.loads(facts or "{}")
        for key, want in (("adapter", "Ethernet 3"), ("address", "192.0.2.37/24"),
                          ("gateway", "192.0.2.1"), ("mac", "AA:BB:CC:DD:EE:01"),
                          ("link", "1 Gbps")):
            check("facts." + key, facts.get(key) == want,
                  f"(got {facts.get(key)!r}, wanted {want!r})")
        # a heuristic that shows its reasoning can be corrected; one that just
        # asserts has to be trusted blindly
        check("facts.says_why_this_adapter",
              "library devices" in (facts.get("chosen") or ""), f"({facts})")

        rows = poll(window, """(function () {
            var r = [...document.querySelectorAll('.bv-drop .net-row')];
            if (!r.length) return '';
            return JSON.stringify(r.map(function (x) {
                return { dot: x.children[0].className,
                         name: x.children[1].textContent,
                         // identity lives in the attribute: the ip CELL is blank
                         // for a device we can name, by design
                         ip: x.getAttribute('data-ip'),
                         shown_ip: x.children[2].textContent,
                         tag: x.children[3].textContent };
            }));
        })()""")
        rows = json.loads(rows or "[]")
        check("panel.lists_the_segment", len(rows) >= 4, f"(got {len(rows)})")

        by_ip = {r["ip"]: r for r in rows}
        gw = by_ip.get("192.0.2.1") or {}
        check("panel.gateway_is_marked", "gw" in gw.get("tag", ""), f"({gw})")

        stranger = by_ip.get("192.0.2.77") or {}
        check("panel.stranger_flagged", "not in library" in stranger.get("tag", ""),
              f"({stranger})")

        # only the GATEWAY shows an address (Jake's call): every other row reads
        # by name, or by vendor / "unknown device" for a stranger, with the
        # address and mac kept a hover away in the tooltip.
        named = by_ip.get("192.0.2.40") or {}
        check("panel.named_device_hides_its_ip", named.get("shown_ip") == "",
              f"({named})")
        check("panel.stranger_hides_its_ip_too",
              stranger.get("shown_ip") == "", f"({stranger})")
        check("panel.stranger_still_reads_as_something",
              bool(stranger.get("name") and stranger.get("name") != "—"),
              f"({stranger})")
        check("panel.gateway_is_the_one_address_shown",
              (by_ip.get("192.0.2.1") or {}).get("shown_ip") == "192.0.2.1",
              f"({by_ip.get('192.0.2.1')})")
        check("panel.stranger_ip_in_tooltip", js(window, """(function () {
            var r = [...document.querySelectorAll('.bv-drop .net-row')]
                .find(function (x) { return x.getAttribute('data-ip') === '192.0.2.77'; });
            return !!r && r.title.indexOf('192.0.2.77') === 0;
        })()"""))
        check("panel.ip_stays_in_the_tooltip", js(window, """(function () {
            var r = [...document.querySelectorAll('.bv-drop .net-row')]
                .find(function (x) { return x.getAttribute('data-ip') === '192.0.2.40'; });
            return !!r && r.title.indexOf('192.0.2.40') === 0;
        })()"""))

        # the folds must survive the 2s repaint - rebuilding the panel wholesale
        # used to snap the adapter section shut within a tick of opening it
        js(window, """(function () {
            var s = [...document.querySelectorAll('.bv-drop .net-sec')].find(function (x) {
                var h = x.querySelector('.net-sec-head');
                return h && h.textContent.indexOf('adapter') >= 0;
            });
            s.querySelector('.net-sec-head').click();
        })()""")
        opened_fold = js(window, """(function () {
            var s = [...document.querySelectorAll('.bv-drop .net-sec')].find(function (x) {
                var h = x.querySelector('.net-sec-head');
                return h && h.textContent.indexOf('adapter') >= 0;
            });
            return s.classList.contains('open');
        })()""")
        check("panel.adapter_fold_opens", opened_fold is True)
        js(window, "BV.netstatus._render(window.__probe_payload)")
        time.sleep(0.2)
        check("panel.fold_survives_a_repaint", js(window, """(function () {
            var s = [...document.querySelectorAll('.bv-drop .net-sec')].find(function (x) {
                var h = x.querySelector('.net-sec-head');
                return h && h.textContent.indexOf('adapter') >= 0;
            });
            return !!s && s.classList.contains('open');
        })()"""))

        # THE anti-lie lock: a library device with no neighbour entry is hollow.
        # If this ever renders as `gone`, the panel is calling a healthy camera
        # dead purely because nobody has talked to it yet.
        absent = by_ip.get("192.0.2.50")
        if absent is None:
            # it may be folded into the collapsed "not heard from" group
            absent = json.loads(js(window, """(function () {
                var r = [...document.querySelectorAll('.bv-drop .net-row')]
                    .find(function (x) { return x.children[2].textContent === '192.0.2.50'; });
                return r ? JSON.stringify({ dot: r.children[0].className }) : 'null';
            })()""") or "null")
        check("panel.absent_is_present_at_all", absent is not None)
        if absent:
            check("panel.absent_is_hollow", "absent" in absent.get("dot", ""), f"({absent})")
            check("panel.absent_is_not_a_fault", "gone" not in absent.get("dot", ""),
                  f"({absent})")

        check("panel.scope_is_stated", js(window, """(function () {
            var f = document.querySelector('.bv-drop .net-foot');
            return !!f && f.textContent.indexOf('this subnet only') >= 0;
        })()"""))
        # something to check -> the button is there
        check("panel.check_now_offered", js(window, """(function () {
            var b = [...document.querySelectorAll('.bv-drop .net-sub .btn')]
                .find(function (x) { return x.textContent === 'check now'; });
            return !!b && !b.classList.contains('hidden');
        })()"""))

        # every colour must come from the theme variables, never a literal - the
        # app reskins into 28 themes, so a hardcoded green would survive here and
        # then look wrong on 27 of them. Comparing the painted colour against the
        # resolved variable is what actually proves it.
        themed = js(window, """(function () {
            function rgb(c) {
                var d = document.createElement('div');
                d.style.color = c; document.body.appendChild(d);
                var v = getComputedStyle(d).color; d.remove(); return v;
            }
            var css = getComputedStyle(document.documentElement);
            var live = document.querySelector('.bv-drop .net-dot.live');
            var absent = document.querySelector('.bv-drop .net-dot.absent');
            return JSON.stringify({
                live: getComputedStyle(live).backgroundColor,
                ok: rgb(css.getPropertyValue('--ok').trim()),
                absent_bg: getComputedStyle(absent).backgroundColor,
            });
        })()""")
        themed = json.loads(themed or "{}")
        check("theme.live_dot_is_the_ok_variable",
              themed.get("live") and themed.get("live") == themed.get("ok"),
              f"({themed})")
        # hollow means literally no fill - if this ever becomes opaque, `absent`
        # has started looking like a verdict
        check("theme.absent_dot_is_transparent",
              "rgba(0, 0, 0, 0)" == themed.get("absent_bg"), f"({themed})")

        # ---- a stale pin ----
        # A pin to an adapter that is not in the table - the dongle out, or a pin
        # left over from another dock/PC/VM - used to leave an empty panel with
        # a raw slug for a reason and the only way out folded shut at the bottom.
        # The sentence comes from the real classifier: this is the seam.
        stale = discover.classify_state(None, "pinned-missing", [], missing="Ethernet 9")[1]
        stub(window, payload(state="no-link", why="pinned-missing", adapter=None,
                             cidr="", gateway="", detail=stale, devices=[], seen=0,
                             pin={"mac": "AA:BB:CC:DD:EE:09", "name": "Ethernet 9"}))
        js(window, "BV.netstatus._render(window.__probe_payload)")
        time.sleep(0.3)
        facts = json.loads(js(window, """(function () {
            var o = {};
            document.querySelectorAll('.bv-drop .net-fact').forEach(function (r) {
                o[r.children[0].textContent] = r.children[1].textContent;
            });
            return JSON.stringify(o);
        })()""") or "{}")
        check("stale.reason_is_words_not_a_slug",
              facts.get("chosen") not in ("", "pinned-missing", "—")
              and "pinned" in (facts.get("chosen") or ""), f"({facts.get('chosen')!r})")
        check("stale.no_adapter_facts_are_blank",
              facts.get("gateway") == "—" and facts.get("address") == "—", f"({facts})")
        check("stale.detail_names_the_pin", js(window, """(function () {
            var d = document.querySelector('.bv-drop .net-head-detail');
            return !!d && d.textContent.indexOf('Ethernet 9') >= 0
                && d.textContent.indexOf('pinned') >= 0;
        })()"""))
        btns = json.loads(js(window, """JSON.stringify(
            [...document.querySelectorAll('.bv-drop .net-sub .btn')].map(function (b) {
                return { t: b.textContent, hidden: b.classList.contains('hidden') }; }))""")
            or "[]")
        by_t = {b["t"]: b for b in btns}
        check("stale.way_out_is_offered",
              by_t.get("use automatic", {}).get("hidden") is False, f"({btns})")
        check("stale.nothing_to_check_so_no_button",
              by_t.get("check now", {}).get("hidden") is True, f"({btns})")
        check("stale.no_segment_no_list", js(window, """(function () {
            var s = [...document.querySelectorAll('.bv-drop .net-sec')].find(function (x) {
                return x.textContent.indexOf('on this switch') >= 0; });
            return !!s && s.classList.contains('hidden');
        })()"""))
        # the picker shows the ghost as the choice in force, not "automatic"
        picks = json.loads(js(window, """JSON.stringify(
            [...document.querySelectorAll('.bv-drop .net-pick-row')].map(function (b) {
                return { t: b.textContent, on: b.classList.contains('on'),
                         dot: (b.querySelector('.net-dot') || {}).className || '' }; }))""")
            or "[]")
        ghost = next((r for r in picks if "Ethernet 9" in r["t"]), None)
        auto = next((r for r in picks if r["t"].startswith("automatic")), None)
        check("stale.picker_lists_the_ghost", ghost is not None, f"({picks})")
        check("stale.ghost_is_the_choice_in_force",
              bool(ghost) and ghost["on"] and "not present" in ghost["t"]
              and "absent" in ghost["dot"], f"({ghost})")
        check("stale.automatic_is_not_claimed", bool(auto) and not auto["on"], f"({auto})")
        # and the way out actually clears the setting
        js(window, """window.__set = null;
            window.pywebview.api.set_setting = function (k, v) {
                window.__set = [k, v]; return Promise.resolve({ ok: true, data: true });
            };
            [...document.querySelectorAll('.bv-drop .net-sub .btn')]
                .find(function (b) { return b.textContent === 'use automatic'; }).click();""")
        cleared = poll(window, "window.__set ? JSON.stringify(window.__set) : ''")
        check("stale.use_automatic_clears_the_pin",
              cleared == '["net_adapter",null]', f"(got {cleared!r})")

        # esc closes it (wireDismiss listens on window, so this reaches it)
        js(window, "window.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        time.sleep(0.4)
        check("panel.escape_closes",
              not js(window, "!!document.querySelector('.bv-drop .net-panel')"))

        # ---- the poller stays out of the way ----
        js(window, "BV.modal('probe', BV.el('div', {}, 'x'))")
        time.sleep(0.2)
        check("tick.pauses_for_a_modal", js(window, "BV.netstatus._shouldTick()") is False)
        # BV.modal listens on document (capture) while BV.dropPanel listens on
        # window, so this one has to be dispatched at document - it bubbles up to
        # window, whereas an event dispatched AT window never reaches document
        js(window, "document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        check("tick.modal_closed", poll(window, "!BV.modalOpen()") is True)
        # This window is HIDDEN, so document.hidden is permanently true and the
        # poller is correctly idle here for the whole run - which is exactly the
        # behaviour we want on the desktop too. So the invariant to assert is
        # that once the modal is gone, visibility is the ONLY thing still gating
        # a tick; asserting a bare `true` would be unsatisfiable in this window.
        check("tick.only_visibility_still_gates_it",
              poll(window, "BV.netstatus._shouldTick() === !document.hidden") is True)

        # reduced-motion and scroll-to-close are NOT probe-testable here: the
        # probe window emulates no media queries and fires no native scroll
        # events. They are covered by review, not by this file.
        report()
    except Exception as e:  # noqa: BLE001
        print("[FAIL] probe crashed:", type(e).__name__, e)
        FAILURES.append("crash")
    finally:
        window.destroy()


def main():
    lib = _TMP / "lib"
    lib.mkdir(parents=True)
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
    api.bind(window)
    webview.start(probe, window, gui="edgechromium")
    sys.exit(exit_code())


if __name__ == "__main__":
    main()
