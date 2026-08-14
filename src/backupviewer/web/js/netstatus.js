/* netstatus.js - the plant-link pill (BV.netstatus).

   Answers one question a tech asks constantly on the floor: when a backup
   fails, is it me, the network, or the device? The pill in the statusbar reads
   this laptop's own adapter, gateway and neighbour tables - the switch is never
   contacted - and clicking it drops a panel listing what is actually on this
   segment, merged with the library.

   Two contracts worth knowing before editing:
   - The pill button is built ONCE and mutated in place. BV.dropPanel's dismiss
     logic holds the anchor by reference, so replacing the element (or its
     parent's innerHTML) silently breaks the open/close toggle. That is also why
     the pill lives in its own #status-net span rather than inside the two the
     router rebuilds constantly.
   - BV.dropPanel returns null when a click is the swallowed half of a toggle;
     that is a close, not a failure. */
(function () {
  "use strict";

  var TICK_MS = 2000;          /* same beat as the camera tiles (home.js) */
  var HIT_MS = 600;            /* how long a "just answered" mark stays up */

  /* state -> [pill text, pill variant]. `unknown` deliberately gets the neutral
     ghost chip: when the READ fails we have no verdict, and a tool that turns
     red when it itself breaks teaches people to ignore it. */
  var LOOK = {
    ok: ["connected", "ok-soft"],
    "no-gateway": ["no gateway", "warn"],
    "no-ip": ["no ip", "err"],
    "no-link": ["no link", "err"],
    "no-adapter": ["no plant adapter", "off"],
    unknown: ["link ?", "ghost"],
  };
  /* the states worth interrupting for; a gateway that ARPs intermittently would
     otherwise toast on every flap */
  var LOUD = { "no-link": 1, "no-ip": 1 };

  var btn = null, dot = null, label = null;
  var timer = null, inFlight = false, panel = null, panelBody = null;
  var last = null, lastState = null, booted = false;
  var prevLive = {};           /* ip -> was it reachable on the previous tick */
  var rowEls = {};             /* ip -> row element, so repaints patch in place */
  var skipped = 0;             /* addresses a capped sweep did not reach */

  function look(state) { return LOOK[state] || LOOK.unknown; }

  function ago(ms) {
    var s = Math.round((ms || 0) / 1000);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.round(s / 60) + "m";
    return Math.round(s / 3600) + "h";
  }

  /* ---- the pill ------------------------------------------------------------ */

  function ensureBtn() {
    if (btn) return btn;
    var slot = document.getElementById("status-net");
    if (!slot) return null;
    btn = BV.el("button", { class: "pill ghost net-pill", title: "plant link" });
    dot = BV.el("span", { class: "net-dot" });
    label = BV.el("span", {}, "");
    btn.appendChild(dot);
    btn.appendChild(label);
    btn.addEventListener("click", toggle);
    slot.appendChild(btn);
    return btn;
  }

  function render(p) {
    if (!ensureBtn()) return;
    last = p;
    var l = look(p.state);
    var text = l[0];
    /* how long a fault has held is the actionable half of "no ip" - it tells
       you whether to wait for DHCP or go move the cable */
    if (p.state === "no-ip" || p.state === "no-link") text += " · " + ago(p.since_ms);
    label.textContent = text;
    btn.className = "pill " + l[1] + " net-pill" + (p.probe_ok ? "" : " unread");
    dot.className = "net-dot " + (p.state === "ok" ? "live"
      : p.state === "unknown" ? "unknown" : "gone");
    var a = p.adapter || {};
    btn.title = (p.detail || text)
      + (a.name ? "\n" + a.name + (a.ip ? " · " + a.ip : "") : "")
      + (p.probe_ok ? "" : "\n(could not read the adapter tables just now)");
    if (panel) paintPanel(p);
  }

  function announce(p) {
    if (!booted) { booted = true; lastState = p.state; return; }  /* first read isn't a change */
    if (p.state === lastState) return;
    var was = lastState;
    lastState = p.state;
    if (LOUD[p.state]) BV.toast("plant link lost — " + look(p.state)[0], 2600);
    else if (p.state === "ok" && was && was !== "unknown") BV.toast("plant link back");
  }

  /* ---- polling ------------------------------------------------------------- */

  function shouldTick() {
    if (inFlight) return false;                 /* never stack calls */
    if (document.hidden) return false;
    if (BV.modalOpen && BV.modalOpen()) return false;
    if (document.querySelector(".cvx-remote")) return false;
    return true;
  }

  function tick(force) {
    if (!force && !shouldTick()) return;
    inFlight = true;
    BV.api.call("net_status", !!panel).then(function (p) {
      render(p);
      announce(p);
      if (BV.state && BV.state.emit) BV.state.emit("net", p);
    }).catch(function () {
      /* a failed bridge call is not evidence either way - leave the last
         verdict alone rather than inventing one */
    }).then(function () { inFlight = false; });
  }

  /* ---- the panel ----------------------------------------------------------- */

  function row(d) {
    var el = BV.el("div", { class: "net-row" });
    el.appendChild(BV.el("span", { class: "net-dot" }));
    el.appendChild(BV.el("span", { class: "net-name" }, ""));
    el.appendChild(BV.el("span", { class: "net-ip" }, ""));
    el.appendChild(BV.el("span", { class: "net-tag" }, ""));
    return el;
  }

  function paintRow(el, d) {
    var live = d.dot === "live";
    var hit = live && prevLive[d.ip] === false;
    el.children[0].className = "net-dot " + d.dot + (hit ? " hit" : "");
    if (hit) {
      /* the probe window has no requestAnimationFrame and may not fire
         animationend at all, so a timer owns removal */
      (function (node) {
        setTimeout(function () { node.className = node.className.replace(" hit", ""); },
          HIT_MS);
      })(el.children[0]);
    }
    var name = d.name || (d.gateway ? "gateway" : "");
    el.children[1].textContent = name || "—";
    el.children[2].textContent = d.ip;
    var tag = "";
    if (d.gateway) tag = BV.pill("gw", "acc");
    else if (!d.in_library) {
      tag = BV.pill("not in library", "ghost");
      if (d.vendor_kind) el.children[1].textContent = d.vendor_kind + " (unlisted)";
    } else if (d.device_type && d.device_type.indexOf("camera") === 0) {
      tag = BV.pill(d.device_type === "camera-mtx" ? "mtx cam" : "cv-x cam", "acc");
    }
    el.children[3].innerHTML = tag;
    el.title = d.ip + (d.mac ? " · " + d.mac : "")
      + (d.dot === "absent"
        ? "\nno neighbour entry — the laptop hasn't talked to it since the cable went in"
        : d.dot === "gone" ? "\nARP was attempted and got no answer"
        : d.dot === "live" ? "\nanswered just now"
        : "\nknown, not confirmed recently (normal on a quiet network)");
  }

  function paintList(host, rows) {
    var seen = {};
    rows.forEach(function (d) {
      var el = rowEls[d.ip];
      if (!el) { el = rowEls[d.ip] = row(d); }
      paintRow(el, d);
      if (el.parentNode !== host) host.appendChild(el);
      seen[d.ip] = 1;
    });
    Array.prototype.slice.call(host.children).forEach(function (el) {
      var ip = el.children && el.children[2] && el.children[2].textContent;
      if (ip && !seen[ip]) host.removeChild(el);
    });
  }

  function section(title, note) {
    var s = BV.el("div", { class: "net-sec" });
    s.appendChild(BV.el("div", { class: "net-sec-head" }, BV.esc(title)));
    if (note) s.appendChild(BV.el("div", { class: "net-sec-note" }, BV.esc(note)));
    return s;
  }

  function buildPanel() {
    panelBody = BV.el("div", { class: "net-panel" });
    return panelBody;
  }

  function paintPanel(p) {
    if (!panelBody) return;
    var devices = p.devices || [];
    var here = devices.filter(function (d) { return d.dot !== "absent"; });
    var quiet = devices.filter(function (d) { return d.dot === "absent"; });
    var a = p.adapter || {};

    panelBody.innerHTML = "";
    var head = BV.el("div", { class: "net-head" });
    var top = BV.el("div", { class: "net-head-top" });
    top.appendChild(BV.el("span", { class: "net-head-state" }, BV.esc(look(p.state)[0])));
    top.appendChild(BV.el("span", { class: "net-head-where" },
      BV.esc((a.name || "") + (p.cidr ? " · " + p.cidr : ""))));
    head.appendChild(top);
    head.appendChild(BV.el("div", { class: "net-head-detail" }, BV.esc(p.detail || "")));
    panelBody.appendChild(head);

    var sub = BV.el("div", { class: "net-sub" });
    sub.appendChild(BV.el("span", {}, BV.esc(here.length + " answering · " +
      quiet.length + " quiet")));
    var busy = !!p.checking;
    var check = BV.el("button", { class: "btn", title:
      "send one ARP request to each listed address — layer 2 only, no service is touched" },
      busy ? "checking…" : "check now");
    check.disabled = busy;
    check.addEventListener("click", checkNow);
    sub.appendChild(check);
    head.appendChild(sub);
    if (skipped) {
      head.appendChild(BV.el("div", { class: "net-sec-note" },
        BV.esc(skipped + " more were not checked (the sweep is capped)")));
    }

    var s1 = section("on this switch");
    var list = BV.el("div", {});
    s1.appendChild(list);
    panelBody.appendChild(s1);
    rowEls = {};
    paintList(list, here);

    if (quiet.length) {
      var s2 = BV.el("div", { class: "net-sec" });
      var h2 = BV.el("div", { class: "net-sec-head" }, "");
      var b2 = BV.el("div", {});
      s2.appendChild(h2); s2.appendChild(b2);
      BV.collapsible(s2, h2, b2, { open: false });
      h2.appendChild(BV.el("span", {},
        BV.esc("not heard from (" + quiet.length + ")")));
      paintList(b2, quiet);
      panelBody.appendChild(s2);
    }

    panelBody.appendChild(BV.el("div", { class: "net-foot" },
      BV.esc("this segment only — devices on other subnets are reached through "
        + "the gateway and cannot be seen from here. A blink means this laptop's "
        + "neighbour cache just confirmed that device; it is not the switch's "
        + "port LED.")));

    if (p.adapters) panelBody.appendChild(picker(p));

    prevLive = {};
    devices.forEach(function (d) { prevLive[d.ip] = d.dot === "live"; });
  }

  function picker(p) {
    var wrap = BV.el("div", { class: "net-sec" });
    var head = BV.el("div", { class: "net-sec-head" }, "");
    var body = BV.el("div", { class: "net-pick" });
    wrap.appendChild(head); wrap.appendChild(body);
    BV.collapsible(wrap, head, body, { open: false });
    head.appendChild(BV.el("span", {}, "adapter"));
    var chosen = (p.adapter || {}).mac || "";
    var pinned = p.why === "pinned";

    function pick(mac, name) {
      BV.api.call("set_setting", "net_adapter", mac ? { mac: mac, name: name } : null)
        .catch(function () {});
      tick(true);
    }

    var auto = BV.el("button", { class: "net-pick-row" + (pinned ? "" : " on") }, "");
    auto.appendChild(BV.el("span", {}, "automatic"));
    auto.appendChild(BV.el("span", { class: "net-pick-why" },
      BV.esc(pinned ? "" : "chosen by " + (p.why || ""))));
    auto.addEventListener("click", function () { pick(null, null); });
    body.appendChild(auto);

    (p.adapters || []).forEach(function (ad) {
      var b = BV.el("button",
        { class: "net-pick-row" + (pinned && ad.mac === chosen ? " on" : "") }, "");
      b.appendChild(BV.el("span", { class: "net-dot " + (ad.up ? "live" : "absent") }));
      b.appendChild(BV.el("span", { class: "net-name" },
        BV.esc(ad.name + (ad.ip ? " · " + ad.ip : ""))));
      b.appendChild(BV.el("span", { class: "net-pick-why" },
        BV.esc(ad.library ? ad.library + " library" : ad.neighbours + " seen")));
      b.addEventListener("click", function () { pick(ad.mac, ad.name); });
      body.appendChild(b);
    });
    return wrap;
  }

  function checkNow() {
    if (!last || !last.devices) return;
    BV.api.call("net_check", last.devices.map(function (d) { return d.ip; }))
      .then(function (r) {
        skipped = (r && r.skipped) || 0;
        if (r && r.throttled) BV.toast("just checked — give it a moment");
        /* nothing to collect: the ARP replies land in the neighbour table the
           panel already reads, so the dots simply fill in on the next ticks */
        tick(true);
      })
      .catch(function () {});
  }

  function toggle() {
    if (panel) { panel.close(); return; }
    var p = BV.dropPanel(btn, buildPanel(), {
      align: "right",
      onClose: function () { panel = null; panelBody = null; rowEls = {}; },
    });
    if (!p) return;                 /* swallowed half of a toggle - it just closed */
    panel = p;
    if (last) paintPanel(last);
    tick(true);                     /* the panel wants the device list, so re-ask */
  }

  BV.netstatus = {
    STATES: Object.keys(LOOK),
    tick: tick,
    _render: render,
    _shouldTick: shouldTick,
    _look: look,
  };

  BV.api.ready.then(function (ok) {
    /* pop-outs share this laptop's link; a second poller would just double the
       traffic for an identical answer (same reasoning as jobs.js) */
    if (!ok || BV.solo || BV.cvxWin) return;
    tick(true);
    timer = setInterval(tick, TICK_MS);
  });
})();
