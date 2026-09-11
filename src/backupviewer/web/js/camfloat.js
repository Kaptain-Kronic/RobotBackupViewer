/* camfloat.js - BV.camFloats: the camera wall's floating boxes.

   A tile on the multicam wall is 150px tall, which is not big enough to read a
   camera's screen from a step away. Right-clicking a tile pops it out into a
   FLOAT: the same picture in a box you can drag, magnet to the layer's corners
   and edges, resize, zoom, lock in place, and (slice 3) take control of.

   A float is a SLOT, not a camera: the bar's title swaps which camera the box
   is showing, so an arrangement you built outlives what is in it. That is why
   the saved shape is an ordered list rather than a map keyed by camera.

   Every float's picture is fed by the SAME beat as the wall's tiles
   (components/camfeed.js) - one budget, and one CV-X lease map. That is what
   makes popping a camera out cost zero dials: the float and the tile it came
   from are two views of one leased session, and a CV-X controller only has one
   remote slot to give. The wall renders a floating camera as a quiet
   placeholder with no <img> in it, so the picture is only ever fetched once.

   Floats PARK off the library, exactly as a remote chip does on any route: the
   layer hides, every picture inside then fails camfeed's visibility gate, the
   leases stop being renewed and python's reaper hands the slots back. Coming
   back to the library restores the same boxes in the same places. */
(function () {
  "use strict";

  var KEY = "lib_cam_floats";
  var _slots = null;      /* [{id, camId, x,y,w,h, snap, locked, zoom}] - lazy */
  var _boxes = {};        /* slot id -> {box, img, note, zs, cam} */
  var _cams = [];         /* the library's cameras, supplied by home.js */
  var _seq = 0;
  /* ONE box drives a camera at a time, app-wide. Not a technical limit - each
     controller has its own slot - but a deliberate one: every box in control
     is a slot taken off somebody standing at an HMI, and a wall of live
     cursors is not a thing to hand a plant floor. */
  var _armed = null;      /* slot id whose mouse is live */

  function slots() {
    if (_slots === null) {
      var saved = (BV.state.settings || {})[KEY];
      _slots = Array.isArray(saved) ? saved.slice() : [];
      _slots.forEach(function (s) { if (!s.id) s.id = nextId(); });
    }
    return _slots;
  }
  function nextId() { return "f" + (++_seq) + "_" + Date.now().toString(36); }
  var save = BV.debounce(function () {
    if (BV.state.settings) BV.state.settings[KEY] = slots();
    BV.api.call("set_setting", KEY, slots()).catch(function () {});
  }, 500);

  /* one armed box app-wide: arming a second one releases the first, mouse
     button included, so no camera is ever left mid-drag by a handover */
  function disarmAll(except) {
    Object.keys(_boxes).forEach(function (id) {
      if (id !== except && _boxes[id].disarm) _boxes[id].disarm();
    });
  }
  /* clicking away or esc gives the mouse back to the app. The click listener
     is on the CAPTURE phase so it sees the press before anything swallows it,
     and only ever DISARMS - arming is the box's own click. */
  document.addEventListener("click", function (e) {
    if (!_armed) return;
    var b = _boxes[_armed];
    if (b && b.box && b.box.el.contains(e.target)) return;
    disarmAll();
  }, true);
  document.addEventListener("keydown", function (e) {
    if (_armed && e.key === "Escape") { disarmAll(); e.stopPropagation(); }
  }, true);

  function camById(id) {
    for (var i = 0; i < _cams.length; i++) if (_cams[i].id === id) return _cams[i];
    return null;
  }
  function ipOf(cam) { return (cam && cam.ips && cam.ips[0]) || ""; }
  function isCvx(cam) { return !!cam && cam.device_type === "camera-keyence"; }
  function changed() {
    save();
    BV.state.emit("camfloats", slots().length);
  }

  /* ---- one float ---------------------------------------------------------- */

  function build(slot) {
    var cam = camById(slot.camId);
    var live = { cam: cam };

    var box = BV.FloatBox({
      key: slot.id,
      title: cam ? (cam.robot || ipOf(cam) || "(unnamed)") : "(camera not in the library)",
      geom: slot,
      onGeom: function (g) {
        slot.x = g.x; slot.y = g.y; slot.w = g.w; slot.h = g.h;
        slot.snap = g.snap; slot.locked = g.locked;
        save();
      },
      onClose: function () { drop(slot.id); },
    });

    /* the title is the camera picker: swapping keeps the rect and the lock */
    box.titleEl.title = "click to show a different camera in this box";
    box.titleEl.addEventListener("click", function () {
      BV.menu(box.titleEl, _cams.map(function (c) {
        return {
          label: (c.robot || ipOf(c) || "(unnamed)"),
          active: c.id === slot.camId,
          onClick: function () { swap(slot.id, c.id); },
        };
      }));
    });

    /* CONTROL is opt-in and CV-X only: a matrox is driven through its own web
       page, which is the full remote's job, not a picture's. Taking control
       promotes the leased view-only session into a real one (cvx_tile_adopt)
       and swaps the 2s polled still for the live MJPEG stream - safe here
       where it is not on the wall, because only one box controls at a time,
       so exactly one of the browser's six per-origin connections is held. */
    var ctlBtn = BV.el("button", { class: "btn fbox-ctl", type: "button" }, "control");
    var rlBtn = BV.el("button", { class: "btn", type: "button",
      title: "ask this camera for a fresh picture" }, "⟳");
    var zoomBtn = BV.el("button", { class: "btn fbox-zoom", type: "button",
      title: "view zoom — ctrl+scroll inside the box" }, "100%");
    if (isCvx(cam)) box.slot.appendChild(ctlBtn);
    box.slot.appendChild(rlBtn);
    box.slot.appendChild(zoomBtn);

    var stage = BV.el("div", { class: "fbox-stage" });
    var screen = BV.el("div", { class: "fbox-screen wait" });
    var img = BV.el("img", { alt: "", draggable: "false" });
    var note = BV.el("div", { class: "fbox-note" });
    screen.appendChild(img);
    screen.appendChild(note);
    stage.appendChild(screen);
    box.bodyEl.appendChild(stage);

    /* the source aspect is not known until a picture lands (a CV-X screen is
       1024x768, a matrox HMI frame 1920x1200), so the fit re-runs on load */
    var zs = BV.zoomStage(stage, screen, {
      size: function () {
        return { w: img.naturalWidth || 4, h: img.naturalHeight || 3 };
      },
      min: 1, max: 4,
      onZoom: function (z) {
        zoomBtn.textContent = Math.round(z * 100) + "%";
        slot.zoom = z; save();
      },
    });
    box.el.addEventListener("wheel", zs.wheel, { passive: false });
    zoomBtn.addEventListener("click", function () {
      BV.menu(zoomBtn, [100, 150, 200, 300, 400].map(function (p) {
        return { label: p + "%", active: Math.round(zs.zoom() * 100) === p,
                 onClick: function () { zs.setZoom(p / 100); } };
      }));
    });
    img.addEventListener("load", function () { zs.fit(); });
    /* the box itself resizes: re-fit when it does, and when the layer reflows */
    box.el.addEventListener("mouseup", function () { zs.fit(); });

    var ip = ipOf(cam);
    if (!cam) {
      note.textContent = "that camera is no longer in the library";
      box.setStatus("gone", true);
    } else if (!ip) {
      note.textContent = "no IP on record";
      box.setStatus("no ip", true);
    } else {
      BV.camFeed.attach(img, {
        ip: ip, cvx: isCvx(cam),
        onNote: function (text) { note.textContent = text; },
        onState: function (state) {
          screen.classList.remove("wait");   /* a verdict outranks "waiting" */
          screen.classList.toggle("dark", state === "dark");
          box.setStatus(state === "dark" ? "no picture" : "live", state === "dark");
        },
      });
    }

    /* reload = drop the lease and let the next beat dial again. For a matrox
       there is no session at all, so it is just "ask now". */
    rlBtn.addEventListener("click", function () {
      if (!ip) return;
      if (isCvx(cam)) BV.camFeed.release([ip]);
      img._camDue = 0;
      note.textContent = BV.camFeed.NOTE.wait;
      screen.classList.add("wait");
      BV.camFeed._pass();
    });

    /* ---- control ---------------------------------------------------------
       Three states, and the bar says which: view-only (the beat feeds a
       still), controlling but DISARMED (live stream, mouse parked), and armed
       (the mouse drives). One click inside the picture arms it; that click is
       not forwarded, because cvxMouse asks armed() on mousedown and the arm
       only lands on the click after it. */
    var ctl = { sid: null, lease: null, streamUrl: "", on: false };
    var mouse = BV.cvxMouse(screen, {
      sid: function () { return ctl.sid; },
      size: function () {
        return { w: img.naturalWidth || 1024, h: img.naturalHeight || 768 };
      },
      enabled: function () { return _armed === slot.id; },
    });
    function paintCtl() {
      var armed = _armed === slot.id;
      ctlBtn.textContent = ctl.on ? (armed ? "driving" : "control · on") : "control";
      ctlBtn.classList.toggle("on", ctl.on);
      ctlBtn.classList.toggle("armed", armed);
      ctlBtn.title = ctl.on
        ? "giving up control hands this controller's remote slot back"
        : "take control of this camera — it has ONE remote slot, so this takes "
          + "it from whoever is at the HMI";
      box.el.classList.toggle("armed", armed);
      screen.classList.toggle("drivable", ctl.on);
      if (ctl.on) {
        box.setStatus(armed ? "driving · your mouse is on the camera"
                            : "in control — click the picture to drive");
      }
    }
    live.paintCtl = paintCtl;
    live.controlling = function () { return ctl.on; };

    function takeControl() {
      if (ctl.on || !ip || !isCvx(cam)) return;
      var lease = BV.camFeed.take(ip);   /* the beat stops feeding it: we own it now */
      if (!lease || !lease.sid) {
        BV.camFeed.give(ip, lease);
        BV.toast("no live session to take control of yet");
        return;
      }
      ctlBtn.disabled = true;
      BV.api.call("cvx_tile_adopt", lease.sid).then(function (r) {
        ctl.sid = r.session_id; ctl.lease = lease; ctl.on = true;
        ctl.streamUrl = r.stream_url;
        ctlBtn.disabled = false;
        img.src = ctl.streamUrl + "?t=" + Date.now();
        screen.classList.remove("wait", "dark");
        arm();
        paintCtl();
      }).catch(function (e) {
        /* the lease left the feed but nothing took it: hand it straight back,
           or it is a session the reaper no longer knows about and the
           controller's slot is held until the app exits */
        BV.camFeed.give(ip, lease);
        ctlBtn.disabled = false;
        BV.toast("could not take control: " + e.message);
      });
    }
    /* give it back WITHOUT letting go of the slot: cvx_tile_yield demotes the
       session to a view-only lease, the beat picks it up again, and the wall
       tile behind this box is live the moment it comes back */
    function releaseControl() {
      if (!ctl.on) return Promise.resolve();
      var sid = ctl.sid;
      mouse.release();                  /* never leave a camera mid-drag */
      if (_armed === slot.id) _armed = null;
      ctl.on = false; ctl.sid = null;
      img.src = "";                     /* drop the MJPEG connection */
      screen.classList.add("wait");
      note.textContent = BV.camFeed.NOTE.wait;
      paintCtl();
      box.setStatus("");
      return BV.api.call("cvx_tile_yield", sid).then(function (r) {
        BV.camFeed.give(ip, { sid: r.session_id, shotUrl: r.shot_url,
                              streamUrl: r.stream_url });
        img._camDue = 0;
      }).catch(function () {
        /* a session that is neither leased nor owned is reaped by nothing:
           end it rather than strand the controller's slot */
        BV.api.call("cvx_remote_stop", sid).catch(function () {});
      });
    }
    function arm() {
      if (!ctl.on || _armed === slot.id) return;
      disarmAll();
      _armed = slot.id;
      paintCtl();
    }
    live.disarm = function () {
      if (_armed !== slot.id) return;
      mouse.release();
      _armed = null;
      paintCtl();
    };
    /* parked (routed off the library, or an overlay covering us): drop the
       stream but KEEP the session - you come back to the box you left */
    live.park = function (on) {
      if (!ctl.on) return;
      if (on) { live.disarm(); img.src = ""; }
      else if (ctl.streamUrl) img.src = ctl.streamUrl + "?t=" + Date.now();
    };
    live.shutdown = function () {
      mouse.destroy();
      if (!ctl.on) return null;
      if (_armed === slot.id) _armed = null;
      return ctl.sid;
    };
    ctlBtn.addEventListener("click", function () {
      if (ctl.on) releaseControl(); else takeControl();
    });
    screen.addEventListener("click", function () { if (ctl.on) arm(); });
    paintCtl();

    live.box = box; live.img = img; live.note = note; live.zs = zs;
    _boxes[slot.id] = live;
    if (slot.zoom && slot.zoom > 1) zs.setZoom(slot.zoom);
    zs.fit();
    return live;
  }

  /* ---- the collection ----------------------------------------------------- */

  function mount() {
    var list = slots();
    /* build anything new, drop anything gone */
    Object.keys(_boxes).forEach(function (id) {
      if (!list.some(function (s) { return s.id === id; })) {
        var b = _boxes[id];
        delete _boxes[id];
        if (b.box) b.box.destroy(true);
      }
    });
    list.forEach(function (s) { if (!_boxes[s.id]) build(s); });
    BV.floatLayer.show(!!list.length && onLibrary());
    /* Register only while there is something to feed, and register EVERY time
       rather than once: the feed drops a surface whose alive() goes false, and
       the very first sync happens at boot with no boxes open at all - so a
       register-once guard here meant the layer was dropped before a box ever
       existed and never looked at again. camFeed.register replaces in place,
       so calling it per paint is free. */
    if (list.length) {
      BV.camFeed.register("floats", {
        imgs: function () {
          return BV.floatLayer.el().querySelectorAll("img.cam-live");
        },
        alive: function () { return slots().length > 0; },
      });
    } else {
      BV.camFeed.unregister("floats");
    }
    /* the boxes' pictures are fitted to their stages, which only have a size
       once they are laid out in a shown layer */
    list.forEach(function (s) {
      var b = _boxes[s.id];
      if (b && b.zs) b.zs.fit();
    });
  }

  /* floats belong to the library screen: anywhere else they park, which is the
     same contract a remote chip has on every route */
  function onLibrary() {
    return (location.hash || "#home").indexOf("#home") === 0;
  }

  /* The slot leaves the list FIRST: destroy() calls the box's onClose, which
     lands straight back in here, and a missing slot is what stops that. */
  function drop(id) {
    var list = slots();
    var i = list.findIndex(function (s) { return s.id === id; });
    if (i < 0) return;
    var cam = camById(list[i].camId);
    list.splice(i, 1);
    var b = _boxes[id];
    delete _boxes[id];
    /* a box that was CONTROLLING holds a promoted session, and cvx_tile_stop
       is a deliberate no-op for a non-tile sid - calling it here would leak
       the controller's slot in silence. cvx_remote_stop is the one that ends
       a full session. */
    var owned = b && b.shutdown ? b.shutdown() : null;
    if (b && b.box) b.box.destroy(true);
    if (owned) BV.api.call("cvx_remote_stop", owned).catch(function () {});
    /* nothing else may be watching that camera: hand the slot straight back
       rather than making the controller wait out the TTL */
    else if (cam && isCvx(cam) && ipOf(cam)) BV.camFeed.release([ipOf(cam)]);
    if (!slots().length) BV.floatLayer.show(false);
    changed();
  }

  function swap(id, camId) {
    var list = slots();
    var s = list.find(function (x) { return x.id === id; });
    if (!s || s.camId === camId) return;
    var old = camById(s.camId);
    var b0 = _boxes[id];
    var owned = b0 && b0.shutdown ? b0.shutdown() : null;
    if (owned) BV.api.call("cvx_remote_stop", owned).catch(function () {});
    else if (old && isCvx(old) && ipOf(old)) BV.camFeed.release([ipOf(old)]);
    s.camId = camId;
    var b = _boxes[id];
    if (b && b.box) b.box.destroy(true);
    delete _boxes[id];
    build(s);
    changed();
  }

  /* a new box lands where nothing is yet: the first free snap zone, so two
     pop-outs in a row do not stack on top of each other */
  function freeZone() {
    var taken = {};
    slots().forEach(function (s) { if (s.snap) taken[s.snap] = 1; });
    var order = ["nw", "ne", "sw", "se", "w", "e"];
    for (var i = 0; i < order.length; i++) if (!taken[order[i]]) return order[i];
    return "";
  }

  BV.camFloats = {
    /* home.js hands over the library's cameras on every paint: that is when a
       saved arrangement can first be rebuilt, and when a camera that left the
       library stops being findable */
    sync: function (cams) {
      _cams = cams || [];
      mount();
    },
    popOut: function (camId) {
      if (!camId) return;
      var list = slots();
      if (list.some(function (s) { return s.camId === camId; })) {
        BV.camFloats.focusCam(camId);
        return;
      }
      var z = freeZone();
      var g = z ? BV.floatLayer.ZONES[z] : { x: 0.08, y: 0.1, w: 0.44, h: 0.48 };
      list.push({ id: nextId(), camId: camId, x: g.x, y: g.y, w: g.w, h: g.h,
                  snap: z, locked: false, zoom: 1 });
      mount();
      changed();
    },
    has: function (camId) {
      return slots().some(function (s) { return s.camId === camId; });
    },
    count: function () { return slots().length; },
    focusCam: function (camId) {
      var s = slots().find(function (x) { return x.camId === camId; });
      var b = s && _boxes[s.id];
      if (b && b.box) { b.box.flash(); return true; }
      return false;
    },
    /* the openers consult this before dialling: a camera a float already holds
       has no second remote slot to give */
    focusIp: function (ip) {
      if (!ip) return false;
      var hit = slots().find(function (s) {
        var c = camById(s.camId);
        return c && ipOf(c) === ip;
      });
      if (!hit) return false;
      return BV.camFloats.focusCam(hit.camId);
    },
    closeAll: function () {
      slots().slice().forEach(function (s) { drop(s.id); });
    },
    /* lay every box out on the snap grid: 1 -> whole, 2 -> halves, 3-4 ->
       quarters, more than that keeps the quarters and stacks the rest */
    tileThem: function () {
      var list = slots();
      var zones = list.length <= 1 ? ["n"]
        : list.length === 2 ? ["w", "e"]
        : ["nw", "ne", "sw", "se"];
      list.forEach(function (s, i) {
        if (s.locked) return;            /* locked means locked */
        var z = zones[i % zones.length];
        var g = BV.floatLayer.ZONES[z];
        s.snap = z; s.x = g.x; s.y = g.y; s.w = g.w; s.h = g.h;
        var b = _boxes[s.id];
        if (b && b.box) b.box.setGeom(s);
      });
      changed();
    },
    /* called by the router on every route: show on the library, park anywhere
       else. Parking never disturbs the arrangement. */
    syncRoute: function () {
      var on = !!slots().length && onLibrary();
      BV.floatLayer.show(on);
      /* a controlling box drops its MJPEG connection while parked but KEEPS
         the session: you come back to the box you left, still in control */
      Object.keys(_boxes).forEach(function (id) {
        if (_boxes[id].park) _boxes[id].park(!on);
      });
    },
    /* the probe and the wall both need to know who is driving */
    armed: function () { return _armed; },
  };
})();
