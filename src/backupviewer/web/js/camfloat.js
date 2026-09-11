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

    var rlBtn = BV.el("button", { class: "btn", type: "button",
      title: "ask this camera for a fresh picture" }, "⟳");
    var zoomBtn = BV.el("button", { class: "btn fbox-zoom", type: "button",
      title: "view zoom — ctrl+scroll inside the box" }, "100%");
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
    if (b && b.box) b.box.destroy(true);
    /* nothing else may be watching that camera: hand the slot straight back
       rather than making the controller wait out the TTL */
    if (cam && isCvx(cam) && ipOf(cam)) BV.camFeed.release([ipOf(cam)]);
    if (!slots().length) BV.floatLayer.show(false);
    changed();
  }

  function swap(id, camId) {
    var list = slots();
    var s = list.find(function (x) { return x.id === id; });
    if (!s || s.camId === camId) return;
    var old = camById(s.camId);
    if (old && isCvx(old) && ipOf(old)) BV.camFeed.release([ipOf(old)]);
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
      BV.floatLayer.show(!!slots().length && onLibrary());
    },
  };
})();
