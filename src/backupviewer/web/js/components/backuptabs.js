/* components/backuptabs.js - the browser-style backup tabs (#sessionbar) and
   BV.session, the frontend's view of the server session registry.

   One tab per open backup, riding the topbar like a browser: click to
   switch, ✕ / middle-click to close, drag to rearrange (BV.dragReorder,
   axis "x"), right-click for [pop out · close], and TEAR OFF a tab into its
   own OS window by dragging it down out of the strip (or right out of the
   window, which still works). The context menu is the guaranteed path; the
   drags are forgiving enhancements. The strip stays visible on the home
   screen - home is just a screen, the tabs persist - with no tab highlighted
   there.

   State model: BV.state.manifest stays "what THIS window is showing";
   switching tabs re-points it (per-backup UI memory lives in state.js's
   buckets, so every tab restores exactly how you left it). */
(function () {
  "use strict";

  var bar = document.getElementById("sessionbar");
  var onShell = false;      /* router tells us; shell routes highlight no tab */
  var screenLabel = null;   /* breadcrumb segment on the active tab ("io") */

  function find(sid) {
    return BV.session.list.find(function (t) { return t.sid === sid; });
  }

  function labelOf(m) {
    return m.robot_name || m.camera_name || m.name || m.path || "backup";
  }

  BV.session = {
    list: [],          /* {sid, robotId, label, lastHash} in display order */
    currentSid: null,

    /* adopt a freshly opened backup's manifest into the strip (the server
       already made it active). Dedupe by sid: re-opening lands on the
       existing tab. Callers still setManifest + route themselves. */
    open: function (m) {
      if (!m || !m.sid) return;
      var t = find(m.sid);
      if (!t) {
        t = { sid: m.sid, robotId: m.robot_id || null, label: labelOf(m), lastHash: null };
        BV.session.list.push(t);
      } else {
        t.robotId = m.robot_id || t.robotId;
        t.label = labelOf(m);
      }
      BV.session.currentSid = m.sid;
      render();
    },

    /* a dated-backup switch: the SAME tab now shows a different snapshot -
       keep its slot and remembered route, swap its identity */
    retarget: function (oldSid, m) {
      var t = find(oldSid);
      if (!t) { BV.session.open(m); return; }
      t.sid = m.sid;
      t.robotId = m.robot_id || t.robotId;
      t.label = labelOf(m);
      BV.session.currentSid = m.sid;
      render();
    },

    /* browser dedupe for library opens: a robot already on a tab gets
       focused instead of rebuilt. Returns true when it handled the open. */
    focusRobot: function (robotId) {
      if (!robotId) return false;
      var t = BV.session.list.find(function (x) { return x.robotId === robotId; });
      if (!t) return false;
      BV.session.switchTo(t.sid);
      return true;
    },

    switchTo: function (sid) {
      BV.api.call("switch_session", sid).then(function (r) {
        if (r.owner === "popout") return;   /* server fronted its window */
        BV.session.currentSid = sid;
        BV.state.compare = r.compare || null;
        BV.state.setManifest(r.manifest);
        var t = find(sid);
        var want = (t && t.lastHash) || "#overview";
        if (location.hash === want) BV.route();
        else location.hash = want;
        render();
      }).catch(function (e) { BV.toast(e.message); });
    },

    close: function (sid) {
      var t = find(sid);
      if (!t) return;
      BV.api.call("close_session", sid).catch(function () {}).then(function () {
        dropTab(sid, true);
      });
    },

    /* ownership TRANSFERS to the pop-out: the tab leaves the strip without
       closing the session; the pop-out window's close drops it. */
    popOut: function (sid) {
      var t = find(sid);
      if (!t) return;
      BV.api.call("pop_out_backup", sid).then(function () {
        dropTab(sid, false);
      }).catch(function (e) { BV.toast(e.message); });
    },
  };

  /* remove a tab from the strip; killBucket also forgets its UI memory
     (close = end of the backup's lifetime here; pop-out keeps nothing local
     either - the new window builds its own state) */
  function dropTab(sid, killBucket) {
    var t = find(sid);
    if (!t) return;
    var i = BV.session.list.indexOf(t);
    BV.session.list.splice(i, 1);
    if (killBucket !== false) BV.state.dropBucket(sid);
    if (BV.session.currentSid === sid) {
      var next = BV.session.list[i] || BV.session.list[i - 1];
      if (next) { BV.session.switchTo(next.sid); return; }
      BV.session.currentSid = null;
      BV.state.compare = null;
      BV.state.setManifest(null);
      BV.goHome();
    }
    render();
  }

  /* the router notes each non-shell hash so a tab switch lands where you left */
  BV.session.noteHash = function (hash) {
    var t = find(BV.session.currentSid);
    if (t) t.lastHash = hash;
  };
  /* the active tab is a BREADCRUMB: "DD… · io ▾". The router feeds the
     screen name per route (null on shell screens - the segment vanishes,
     tabs read as pure robot names there). Clicking the active tab opens
     the screens menu; every other click still switches tabs. */
  BV.session.setScreen = function (label) {
    if (screenLabel === label) return;
    screenLabel = label;
    render();
  };
  BV.session.setShell = function (shell) {
    if (onShell === shell) return;
    onShell = shell;
    render();
  };

  /* a relocate/merge released sessions server-side: their tabs go quietly */
  BV.state.on("sessions-released", function (sids) {
    var hit = false;
    (sids || []).forEach(function (sid) {
      if (find(sid)) { hit = true; dropTab(sid, true); }
    });
    if (hit) BV.toast("backup moved — tab closed");
  });

  var _dr = BV.dragReorder({
    zones: [bar],
    axis: "x",
    itemSelector: ".stab",
    onDrop: function () {
      var order = Array.prototype.map.call(
        bar.querySelectorAll(".stab"), function (el) { return el.dataset.sid; });
      BV.session.list.sort(function (a, b) {
        return order.indexOf(a.sid) - order.indexOf(b.sid);
      });
    },
  });

  /* where the drag started, in SCREEN coords. A tear-off is judged on the
     DELTA rather than an absolute position, which avoids having to work out
     where the strip sits inside the window chrome - and is the whole reason
     this works when the window is maximized. */
  var dragFromY = null;
  bar.addEventListener("dragstart", function (e) {
    var item = e.target && e.target.closest ? e.target.closest(".stab") : null;
    dragFromY = item ? (e.screenY || 0) : null;
  });

  /* pop out: released outside the window (with margin), OR torn straight DOWN
     out of the strip. Releasing outside is impossible when the window is
     maximized, which is most of the time on a plant PC. Either way the drag
     must not have been dropped on anything - ambiguity is a no-op, never a
     surprise window. */
  var TEAR_DOWN = 60;   /* px below where the drag started */
  bar.addEventListener("dragend", function (e) {
    var item = e.target && e.target.closest ? e.target.closest(".stab") : null;
    var from = dragFromY; dragFromY = null;
    if (!item) return;
    if (e.dataTransfer && e.dataTransfer.dropEffect !== "none") return;
    var M = 40;
    var out = e.screenX < window.screenX - M
      || e.screenX > window.screenX + window.outerWidth + M
      || e.screenY < window.screenY - M
      || e.screenY > window.screenY + window.outerHeight + M;
    /* dragReorder owns horizontal motion inside the strip and accepts those
       drops, so a reorder never reaches here with dropEffect "none" */
    var torn = from !== null && (e.screenY - from) > TEAR_DOWN;
    if ((out || torn) && (e.screenX || e.screenY)) BV.session.popOut(item.dataset.sid);
  });

  function render() {
    if (!bar) return;
    bar.innerHTML = "";
    BV.session.list.forEach(function (t) {
      var active = !onShell && t.sid === BV.session.currentSid;
      var el = BV.el("div", { class: "stab" + (active ? " active" : ""), title: t.sid });
      el.dataset.sid = t.sid;
      el.appendChild(BV.el("span", { class: "stab-label" }, BV.esc(t.label)));
      if (active && screenLabel) {
        el.appendChild(BV.el("span", { class: "stab-screen",
          title: "screens (1-9, 0, -, =)" }, "· " + BV.esc(screenLabel) + " ▾"));
      }
      var x = BV.el("button", { class: "stab-x", title: "close" }, "✕");
      x.addEventListener("click", function (e) {
        e.stopPropagation();
        BV.session.close(t.sid);
      });
      el.appendChild(x);
      el.addEventListener("click", function () {
        if (_dr.isRecentDrag()) return;
        if (onShell || t.sid !== BV.session.currentSid) BV.session.switchTo(t.sid);
        /* clicking the tab you are already ON asks "where within?" */
        else if (screenLabel && BV.screensMenu) BV.screensMenu(el);
      });
      el.addEventListener("auxclick", function (e) {
        if (e.button === 1) BV.session.close(t.sid);
      });
      el.addEventListener("contextmenu", function (e) {
        e.preventDefault();
        BV.menu({ x: e.clientX, y: e.clientY }, [
          { label: "pop out", onClick: function () { BV.session.popOut(t.sid); } },
          { label: "close", onClick: function () { BV.session.close(t.sid); } },
        ]);
      });
      _dr.wire(el);
      bar.appendChild(el);
    });
    bar.classList.toggle("hidden", !BV.session.list.length);
  }
})();
