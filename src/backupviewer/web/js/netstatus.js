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
  var ui = null;               /* the panel's nodes, built once, updated in place */
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

  function speed(bps) {
    if (!bps) return "—";
    if (bps >= 1e9) return (bps / 1e9).toFixed(bps % 1e9 ? 1 : 0) + " Gbps";
    if (bps >= 1e6) return Math.round(bps / 1e6) + " Mbps";
    return Math.round(bps / 1e3) + " Kbps";
  }

  /* say WHY this adapter was picked - a heuristic that shows its reasoning can
     be corrected; one that just asserts has to be trusted blindly */
  function why(w) {
    return {
      pinned: "you pinned it",
      library: "most library devices are on its subnet",
      neighbours: "the busiest wired segment",
      remembered: "it was the plant link a moment ago",
    }[w] || (w || "—");
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
    /* the ip is the row's identity for patching, and it must live in an
       attribute rather than in the ip cell - that cell is blank for a device we
       can name, so reading identity off it would lose the row */
    var el = BV.el("div", { class: "net-row", "data-ip": d.ip });
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
    var tag = "";
    if (d.gateway) tag = BV.pill("gw", "acc");
    else if (!d.in_library) {
      tag = BV.pill("not in library", "ghost");
      name = d.vendor_kind ? d.vendor_kind + " (unlisted)" : "unknown device";
    } else if (d.device_type && d.device_type.indexOf("camera") === 0) {
      tag = BV.pill(d.device_type === "camera-mtx" ? "mtx cam" : "cv-x cam", "acc");
    }
    el.children[1].textContent = name || "—";
    /* only the gateway shows its address - it is the one row whose address the
       facts block cross-references and the one a tech types into a ping. Every
       other row reads by name (or by vendor for a stranger); the address and
       mac stay a hover away in the tooltip, so nothing is lost, and the list
       stops being a wall of numbers. */
    el.children[2].textContent = d.gateway ? d.ip : "";
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
      var ip = el.getAttribute("data-ip");
      if (ip && !seen[ip]) host.removeChild(el);
    });
  }

  var FOOT = "this segment only — devices on other subnets are reached through "
    + "the gateway and cannot be seen from here. A blink means this laptop's "
    + "neighbour cache just confirmed that device; it is not the switch's port LED.";
  var FACTS = ["adapter", "address", "gateway", "mac", "link", "holding", "chosen"];

  /* The panel is built ONCE here and updated in place by paintPanel. It used to
     be rebuilt from scratch on every 2s tick, which quietly threw away whatever
     the reader had done: opening the adapter section snapped it shut within a
     tick. A surface that repaints under someone must never reset their state. */
  function buildPanel() {
    panelBody = BV.el("div", { class: "net-panel" });
    ui = { facts: {}, picks: {} };

    var head = BV.el("div", { class: "net-head" });
    var top = BV.el("div", { class: "net-head-top" });
    ui.state = BV.el("span", { class: "net-head-state" }, "");
    ui.where = BV.el("span", { class: "net-head-where" }, "");
    top.appendChild(ui.state);
    top.appendChild(ui.where);
    head.appendChild(top);
    ui.detail = BV.el("div", { class: "net-head-detail" }, "");
    head.appendChild(ui.detail);

    var facts = BV.el("div", { class: "net-facts" });
    FACTS.forEach(function (k) {
      var r = BV.el("div", { class: "net-fact" });
      r.appendChild(BV.el("span", { class: "net-fact-k" }, BV.esc(k)));
      ui.facts[k] = BV.el("span", { class: "net-fact-v" }, "");
      r.appendChild(ui.facts[k]);
      facts.appendChild(r);
    });
    head.appendChild(facts);

    var sub = BV.el("div", { class: "net-sub" });
    ui.count = BV.el("span", {}, "");
    ui.check = BV.el("button", { class: "btn", title:
      "send one ARP request to each listed address — layer 2 only, no service is touched" },
      "check now");
    ui.check.addEventListener("click", checkNow);
    sub.appendChild(ui.count);
    sub.appendChild(ui.check);
    head.appendChild(sub);
    ui.skipNote = BV.el("div", { class: "net-sec-note hidden" }, "");
    head.appendChild(ui.skipNote);
    panelBody.appendChild(head);

    var s1 = BV.el("div", { class: "net-sec" });
    s1.appendChild(BV.el("div", { class: "net-sec-head" }, "on this switch"));
    ui.hereList = BV.el("div", {});
    s1.appendChild(ui.hereList);
    panelBody.appendChild(s1);

    ui.quietSec = BV.el("div", { class: "net-sec" });
    var qh = BV.el("div", { class: "net-sec-head" }, "");
    ui.quietList = BV.el("div", {});
    ui.quietSec.appendChild(qh);
    ui.quietSec.appendChild(ui.quietList);
    BV.collapsible(ui.quietSec, qh, ui.quietList, { open: false });
    ui.quietLabel = BV.el("span", {}, "");
    qh.appendChild(ui.quietLabel);
    panelBody.appendChild(ui.quietSec);

    panelBody.appendChild(BV.el("div", { class: "net-foot" }, BV.esc(FOOT)));

    ui.pickSec = BV.el("div", { class: "net-sec" });
    var ph = BV.el("div", { class: "net-sec-head" }, "");
    ui.pickBody = BV.el("div", { class: "net-pick" });
    ui.pickSec.appendChild(ph);
    ui.pickSec.appendChild(ui.pickBody);
    BV.collapsible(ui.pickSec, ph, ui.pickBody, { open: false });
    ph.appendChild(BV.el("span", {}, "adapter"));
    ui.pickAuto = BV.el("button", { class: "net-pick-row" }, "");
    ui.pickAuto.appendChild(BV.el("span", {}, "automatic"));
    ui.pickAutoWhy = BV.el("span", { class: "net-pick-why" }, "");
    ui.pickAuto.appendChild(ui.pickAutoWhy);
    ui.pickAuto.addEventListener("click", function () { pick(null, null); });
    ui.pickBody.appendChild(ui.pickAuto);
    panelBody.appendChild(ui.pickSec);

    return panelBody;
  }

  function pick(mac, name) {
    BV.api.call("set_setting", "net_adapter", mac ? { mac: mac, name: name } : null)
      .catch(function () {});
    tick(true);
  }

  /* Updates the skeleton buildPanel made. Touches text and classes only - no
     node is replaced - so folds the reader opened stay open and the scroll
     position holds while the data underneath keeps ticking. */
  function paintPanel(p) {
    if (!ui) return;
    var devices = p.devices || [];
    var here = devices.filter(function (d) { return d.dot !== "absent"; });
    var quiet = devices.filter(function (d) { return d.dot === "absent"; });
    var a = p.adapter || {};

    ui.state.textContent = look(p.state)[0];
    ui.where.textContent = (a.name || "") + (p.cidr ? " · " + p.cidr : "");
    ui.detail.textContent = p.detail || "";

    /* the full connection picture, so nobody has to go hunting in ipconfig:
       everything the OS told us about this link, and nothing inferred */
    ui.facts.adapter.textContent = a.name || "—";
    ui.facts.address.textContent = a.ip ? a.ip + (a.prefix ? "/" + a.prefix : "") : "none";
    ui.facts.gateway.textContent = p.gateway || "none configured";
    ui.facts.mac.textContent = a.mac || "—";
    ui.facts.link.textContent = speed(a.speed);
    ui.facts.holding.textContent = ago(p.since_ms) + " in this state";
    ui.facts.chosen.textContent = why(p.why);

    ui.count.textContent = here.length + " answering · " + quiet.length + " quiet";
    var busy = !!p.checking;
    ui.check.textContent = busy ? "checking…" : "check now";
    ui.check.disabled = busy;
    ui.skipNote.textContent = skipped
      ? skipped + " more were not checked (the sweep is capped)" : "";
    ui.skipNote.classList.toggle("hidden", !skipped);

    paintList(ui.hereList, here);
    ui.quietSec.classList.toggle("hidden", !quiet.length);
    ui.quietLabel.textContent = "not heard from (" + quiet.length + ")";
    paintList(ui.quietList, quiet);

    paintPicker(p);

    prevLive = {};
    devices.forEach(function (d) { prevLive[d.ip] = d.dot === "live"; });
  }

  function paintPicker(p) {
    var chosen = (p.adapter || {}).mac || "";
    var pinned = p.why === "pinned";
    ui.pickAuto.classList.toggle("on", !pinned);
    ui.pickAutoWhy.textContent = pinned ? "" : "chosen by " + (p.why || "");

    var seen = {};
    (p.adapters || []).forEach(function (ad) {
      var b = ui.picks[ad.mac];
      if (!b) {
        b = ui.picks[ad.mac] = BV.el("button", { class: "net-pick-row" }, "");
        b.appendChild(BV.el("span", { class: "net-dot" }));
        b.appendChild(BV.el("span", { class: "net-name" }, ""));
        b.appendChild(BV.el("span", { class: "net-pick-why" }, ""));
        b.addEventListener("click", function () { pick(ad.mac, ad.name); });
      }
      b.children[0].className = "net-dot " + (ad.up ? "live" : "absent");
      b.children[1].textContent = ad.name + (ad.ip ? " · " + ad.ip : "");
      b.children[2].textContent = ad.library
        ? ad.library + " library" : ad.neighbours + " seen";
      b.classList.toggle("on", pinned && ad.mac === chosen);
      if (b.parentNode !== ui.pickBody) ui.pickBody.appendChild(b);
      seen[ad.mac] = 1;
    });
    Object.keys(ui.picks).forEach(function (mac) {
      if (!seen[mac] && ui.picks[mac].parentNode) {
        ui.picks[mac].parentNode.removeChild(ui.picks[mac]);
        delete ui.picks[mac];
      }
    });
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
    /* mounted into the bottom slab rather than floated: pinned by its bottom
       edge it grows UPWARD as devices arrive and as the folds open, with no
       measurement to get wrong. Floating it measured an empty box and dropped
       the real content off the bottom of the screen. Being pinned also means
       scrolling the library no longer closes it — you can watch the dots while
       you work. */
    var content = buildPanel();
    if (last) paintPanel(last);
    var p = BV.dropPanel(btn, content, {
      className: "net-drop",
      mount: document.getElementById("chrome-bottom"),
      onClose: function () {
        panel = null; panelBody = null; ui = null; rowEls = {};
      },
    });
    if (!p) { panelBody = null; ui = null; return; }  /* swallowed half of a toggle */
    panel = p;
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
