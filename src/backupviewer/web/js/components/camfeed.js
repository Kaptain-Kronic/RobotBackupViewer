/* components/camfeed.js - one beat feeding every live camera picture in the app.

   The multicam wall's tiles and the floating camera boxes are two SURFACES over
   one feed: the same loader, the same per-beat budget and - the part that
   actually matters - the same CV-X lease map. A CV-X controller has a single
   remote slot, so a tile and a float looking at the same camera must share one
   session or the second one gets CVX_BUSY.

   A surface REGISTERS itself with a way to list its <img class="cam-live">
   elements and a way to say whether it is still on screen; the beat runs while
   at least one live surface is registered and stops when the last leaves.
   Each img is wired by attach(), which owns its whole load lifecycle - the
   honesty timer, the retry backoff, the dial for a CV-X lease - and reports
   back through callbacks, so a surface can dress the states however it likes.

   BOTH vendors POLL a still picture: a matrox serves its HMI jpeg over plain
   http, a CV-X its mirrored screen through the remote bridge (dial once for the
   lease, then ask that lease for a frame each beat). Nothing here holds a
   stream open - a wall of never-ending responses starves on the browser's
   six-connections-per-origin cap, and every tile past the sixth then sits dark
   forever; cvx_remote.SHOT_PATH has the long form. Taking CONTROL of a float is
   the one place a stream is ever opened, and camfloat.js owns that after it has
   called take(). */
(function () {
  "use strict";

  var CAM_REFRESH_MS = 2000;    /* live picture refresh - a beat gentler than the HMI's 1s */
  /* how many NEW picture fetches one beat may start. The cap is a courtesy to
     the plant network (a wall of 250 KB frames adds up fast), never a limit on
     which cameras are allowed to be live: the tick rotates it, so the cost of
     a big wall is a slower lap, never a picture that stays black forever. */
  var CAM_MAX_LOADS = 6;
  /* how far off-screen still counts as worth fetching, in viewports. Kept
     small on purpose: everything inside this margin competes for the same
     budget, so a generous look-ahead spends the beat on pictures nobody is
     looking at. Something scrolled into view is served on the next beat anyway
     - never-painted outranks refresh - which is what the look-ahead was for. */
  var CAM_NEAR_SCREEN = 0.5;

  /* what a dark picture says. Three different darks, and a tech reads them very
     differently: a held slot is not a dead camera, and a controller that has
     simply not pushed a picture yet is neither. */
  var NOTE = {
    dark: "no image — not answering",
    busy: "in use — another terminal holds it",
    quiet: "connected — no picture yet",
    noHmi: "no HMI image published",
    /* not a verdict — the honest thing to say BEFORE the first picture */
    wait: "waiting for its first frame…",
  };

  /* live CV-X tile sessions, keyed by ip -> {sid, shotUrl, streamUrl}.
     Module-scoped so a re-render (filter keystroke, library refresh, a camera
     popped out of the wall into a float) reuses the live session instead of
     redialing the controller's single remote slot; after a page reload it
     rebuilds from nothing and python's lease reaper collects the orphans. */
  var _cvxTiles = {};
  var _sources = [];            /* {key, imgs, alive} - the wall, the float layer */
  var _timer = null;
  var _sweepDue = false;        /* a surface left: check for orphans next beat */
  var _cursor = 0;              /* whose turn it is: the budget rotates, never restarts */

  /* the Matrox web server's live HMI frame — the same image the wall-monitor
     page shows. Cache-busting belongs to fetchFrame, which is the one place
     either vendor's picture is actually asked for. */
  function camLiveUrl(ip) {
    return "http://" + ip + "/SavedImages/HMIImage.jpg";
  }

  /* ---- one picture's load lifecycle ---------------------------------------

     The img owns its own load lifecycle; the shared beat decides WHEN by
     calling img._camLoad(), never by touching src (reassigning src aborts an
     in-flight transfer and restarts it from byte 0 — a camera needing >2s per
     frame could never complete a single load). No load happens at attach time:
     renders fire on every filter keystroke / library refresh, and fetching
     everything each time hammered the plant network.

     opts: { ip, cvx, onNote(text), onState("live"|"dark") } */
  /* Hand a picture OVER to somebody who owns it, and take it back afterwards.

     Taking control of a floating box promotes its leased session into a full
     one and points the <img> at the live MJPEG stream - but the img still
     carried the class the beat selects on, so two seconds later the beat asked
     for a lease that was no longer there, python found OUR OWN adopted session
     holding the camera and answered CVX_BUSY, and the box wrote "in use -
     another terminal holds it" over the camera being driven. Worse, the
     re-fetch reassigned img.src and killed the stream with it.

     A picture with an owner is simply not the beat's to touch. */
  function detach(img) {
    if (!img) return;
    img.classList.remove("cam-live");
    img._camOwned = 1;
  }
  /* `url` hands the picture back already pointing somewhere. Without it the
     img waits for the next beat, which is up to REFRESH_MS of blank - visible
     as a blink every time a box gives control back. With it there is no gap:
     the still is asked for in the same turn, and the box counts as painted so
     the rotation treats it like any other live picture. */
  function resume(img, url) {
    if (!img) return;
    img._camOwned = 0;
    img.classList.add("cam-live");
    if (url) {
      img._camShown = 1;
      img._camDue = Date.now() + CAM_REFRESH_MS;
      img.src = url + (url.indexOf("?") < 0 ? "?t=" : "&t=") + Date.now();
    } else {
      img._camDue = 0;           /* ask for a picture on the very next beat */
      img._camShown = 0;         /* never-painted goes first in the rotation */
    }
  }

  function attach(img, opts) {
    var ip = opts.ip;
    var isCvx = !!opts.cvx;
    var onNote = opts.onNote || function () {};
    var onState = opts.onState || function () {};
    var pending = 0;       /* Date.now() when the in-flight load started */
    var fails = 0;         /* consecutive failures, for retry backoff */
    var slowTimer = null;

    img.classList.add("cam-live");
    img.dataset.ip = ip;
    if (isCvx) img.dataset.cvx = "1";
    img._camOnState = onState;   /* the sync handler revises a verdict from outside */
    img._camDue = 0;       /* earliest next load; the beat reads this */
    img._camNote = NOTE.dark;   /* WHICH dark this is, if it goes dark */
    img._camShown = 0;     /* has it ever painted? a first picture beats a refresh */

    /* until the first picture lands it SAYS so. A blank black box that explains
       nothing is the one thing a wall must never show: it is indistinguishable
       from a dead camera, and that is exactly how a starved tile used to read.
       Every state carries a note — waiting, dark, or busy — so silence can
       never come back. */
    onNote(NOTE.wait);

    /* one place decides what a dark picture says, so the 8s timer, the error
       handler and the beat cannot disagree — and a verdict can be revised,
       because python knows whether a live session has pushed a picture. */
    img._camSay = function (text) {
      img._camNote = text;
      if (img._camDarkNow) onNote(text);
    };
    function dark() {
      img._camDarkNow = 1;
      onNote(img._camNote);
      onState("dark");
      if (!isCvx) probeMtxDark();
    }
    /* WHY a matrox picture is dark. The img's error event cannot tell us: a 404
       and an unplugged camera look identical from here. One python probe
       separates them — a camera that answers at all is up, and a Design
       Assistant project either publishes SavedImages/HMIImage.jpg or it never
       will — so the answer is stable and worth asking for at most once a
       minute, and only for something that has already gone dark. */
    function probeMtxDark() {
      if (img._camProbeAt && Date.now() - img._camProbeAt < 60000) return;
      img._camProbeAt = Date.now();
      BV.api.call("mtx_tile_probe", { ip: ip }).then(function (r) {
        if (r && r.state === "no_image") img._camSay(NOTE.noHmi);
        else if (r && r.state === "down") img._camSay(NOTE.dark);
      }).catch(function () { /* it keeps whatever it already said */ });
    }
    /* ask for ONE picture, and arm the honesty timer around it: an ABORTED or
       hung load fires no error event, so still nothing after 8s -> say so (a
       frame that lands later clears it). Armed around the fetch and never
       around the dial — the 8s has to measure the picture, not the handshake
       in front of it. */
    function fetchFrame(url) {
      pending = Date.now();
      clearTimeout(slowTimer);
      slowTimer = setTimeout(dark, 8000);
      img.src = url + "?t=" + Date.now();
    }

    img._camLoad = function () {
      if (pending) {
        if (Date.now() - pending < 30000) return false;  /* let it finish first */
        fails++;                    /* hung past any TCP timeout: re-kick */
        dark();
      }
      if (!isCvx) { fetchFrame(camLiveUrl(ip)); return true; }
      var lease = _cvxTiles[ip];
      if (lease && lease.shotUrl) {
        fetchFrame(lease.shotUrl);
        return false;               /* a loopback still is not a plant fetch */
      }
      /* no lease yet: take the controller's one view-only slot, and every beat
         after this one just asks that session for a picture */
      pending = Date.now();
      BV.api.call("cvx_tile_start", { ip: ip, viewer: viewer() })
        .then(function (r) {
        pending = 0;
        img._camSay(NOTE.dark);     /* a fresh dial retires an old verdict */
        _cvxTiles[ip] = { sid: r.session_id, shotUrl: r.shot_url,
                          streamUrl: r.stream_url };
        fetchFrame(r.shot_url);
      }).catch(function (e) {
        pending = 0; fails++;
        /* say WHICH kind of dark this is: a held slot is not a dead cam */
        img._camSay((e && e.code === "CVX_BUSY") ? NOTE.busy : NOTE.dark);
        dark();
        img._camDue = Date.now() + Math.min(CAM_REFRESH_MS * Math.pow(2, fails), 30000);
      });
      return true;                      /* a dial counts against the beat cap */
    };

    img.addEventListener("load", function () {
      pending = 0; fails = 0;
      clearTimeout(slowTimer);
      img._camDarkNow = 0;
      onState("live");
      img._camShown = 1;        /* it has a picture now: it joins the rotation */
      img._camSay(NOTE.dark);
      img._camProbeAt = 0;      /* a camera that started publishing gets re-asked */
      /* everything polls, so everything has a next beat. Nothing parks at
         Infinity any more — that is what used to strand a picture that never
         arrived: unreachable by the tick, and dark until a restart. */
      img._camDue = Date.now() + CAM_REFRESH_MS;
    });
    img.addEventListener("error", function () {
      pending = 0; fails++;
      clearTimeout(slowTimer);
      dark();
      /* 4s, 8s, 16s, then every 30s — a dead camera decays to a slow retry
         instead of being re-polled at full rate forever. A CV-X whose lease
         died under us heals a beat later, when cvx_tile_sync reports the
         session gone and drops it. */
      img._camDue = Date.now() + Math.min(CAM_REFRESH_MS * Math.pow(2, fails), 30000);
    });
  }

  /* ---- the CV-X lease map -------------------------------------------------

     hand the controllers their remote slots back. With no argument that is
     every live session (the CV-X switch, leaving the lens); with a list of ips
     it is only those (a camera the wall picker just took off) — everything
     else is still being watched and must keep mirroring. */
  /* which WINDOW this is, as far as a lease is concerned. Two windows can
     watch one camera - the wall tiles it small while the camera window shows
     it big - and python counts viewers per session, so neither window looking
     away can black the other one out. */
  function viewer() { return BV.camWin ? "camwin" : "main"; }

  function release(ips) {
    (ips || Object.keys(_cvxTiles)).forEach(function (tip) {
      var l = _cvxTiles[tip];
      if (l && l.sid) {
        BV.api.call("cvx_tile_stop", l.sid, viewer()).catch(function () {});
      }
      delete _cvxTiles[tip];
    });
  }
  /* take = the caller owns this session now (it is about to adopt it into a
     full remote), so the beat must stop feeding it AND the reaper must stop
     being told about it. give is the inverse: a float that released control
     hands the session back under the beat rather than dropping the camera. */
  function take(ip) {
    var l = _cvxTiles[ip] || null;
    delete _cvxTiles[ip];
    return l;
  }
  function give(ip, lease) {
    if (lease && lease.sid) _cvxTiles[ip] = lease;
  }

  /* ---- surfaces + the beat ------------------------------------------------ */

  /* Replaces in place rather than unregister-then-add: a surface re-registers
     on every repaint (the wall does it on each filter keystroke), and a sweep
     in that gap would see zero of its imgs and hang up every CV-X session it
     was holding. Only an explicit unregister - or a surface going not-alive -
     is allowed to release anything. */
  function register(key, src) {
    var entry = { key: key, imgs: src.imgs,
                  alive: src.alive || function () { return true; } };
    var i = _sources.findIndex(function (s) { return s.key === key; });
    if (i >= 0) _sources[i] = entry; else _sources.push(entry);
    start();
  }
  /* a surface leaving does NOT hang up every session: another surface may
     still be showing that camera. Release exactly the leases nothing wants any
     more, which for the last surface out is all of them. */
  /* A surface leaving does not sweep on the spot: it is usually leaving in the
     MIDDLE of a repaint that is about to put the same cameras back (the last
     floating box closing re-renders the wall, and sync() runs before the new
     tiles are in the DOM). Sweeping there saw no imgs, called every session an
     orphan, hung them up - and the tiles that appeared a moment later had to
     dial the controllers all over again. The next beat is late enough for the
     DOM to have settled. With no surfaces left at all there is no repaint
     coming, so that case still releases immediately. */
  function unregister(key) {
    var i = _sources.findIndex(function (s) { return s.key === key; });
    if (i < 0) return;
    _sources.splice(i, 1);
    if (!_sources.length) sweep(); else _sweepDue = true;
  }
  /* A surface went away. Guessing from the DOM which sessions are now orphans
     is a race we kept losing: the surface is usually leaving in the middle of
     a repaint that puts the same cameras straight back, and a camera watched
     in ANOTHER WINDOW is not in this document at all. Either way the eager
     guess hung up a live camera and something had to redial it.

     So eagerness is reserved for the places a PERSON asked for it - the CV-X
     switch, the wall picker, closing a box - which all call release() by name.
     Incidental churn is left to python's reaper, which already collects any
     lease nobody renews within CVX_TILE_TTL and counts viewers across windows
     while it does. The one case that still releases here is the last surface
     leaving: nothing is coming back, so there is nothing to race. */
  function sweep() {
    if (_sources.length) return;
    release();
    clearInterval(_timer); _timer = null;
  }
  /* every feedable img across every surface still on screen; dead surfaces are
     dropped as we go, which is what self-stops the beat when a lens flips or
     the library leaves the DOM */
  function liveImgs() {
    var out = [];
    for (var i = _sources.length - 1; i >= 0; i--) {
      var s = _sources[i];
      var ok = false;
      try { ok = s.alive(); } catch (e) { ok = false; }
      if (!ok) { _sources.splice(i, 1); continue; }
      var list = s.imgs() || [];
      for (var j = 0; j < list.length; j++) out.push(list[j]);
    }
    return out;
  }

  /* pause = stop asking. Everything polls a still, so a paused feed has nothing
     to let go of: skipping the pass is the whole pause. The leases simply stop
     being renewed, and python's reaper hands the controllers' single remote
     slots back within CVX_TILE_TTL — one mechanism covering every way a camera
     stops being watched.

     A remote overlay or a modal is up: floats live UNDER both (z 70 vs 80/90),
     so nothing on screen is showing a camera and nothing should be fetching. */
  function paused() {
    if (document.hidden) return true;
    /* the CV-X and MTX remote overlays share the cvx-remote class */
    return !!document.querySelector(".cvx-remote") || BV.modalOpen();
  }

  function pass() {
    if (_sweepDue) { _sweepDue = false; sweep(); }
    var before = _sources.length;
    var imgs = liveImgs();
    /* a surface stopped being alive (the lens flipped, the library left the
       DOM, the float layer parked): hand back every session nothing else is
       watching now, rather than making those controllers wait out the TTL.
       sweep() stops the beat if that was the last surface. */
    if (_sources.length !== before) { sweep(); imgs = liveImgs(); }
    if (!_sources.length) return;
    if (paused()) return;
    var now = Date.now(), kicked = 0, sids = [], showing = [], byIp = {};
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      /* folded/filtered/parked: the house idiom — a raw offsetParent read
         inside a content-visibility subtree forces layout (checklist.js has
         the 42-second receipt), and this loop runs forever on a timer */
      var vis = (img.checkVisibility ? img.checkVisibility() : img.offsetParent !== null);
      if (vis) {
        var r = img.getBoundingClientRect();
        vis = !(r.bottom < -window.innerHeight * CAM_NEAR_SCREEN ||
                r.top > window.innerHeight * (1 + CAM_NEAR_SCREEN));
      }
      if (!vis) continue;        /* scrolled/folded away: stop asking, keep the lease */
      showing.push(img);
      if (img.dataset.cvx) {
        byIp[img.dataset.ip] = img;
        /* renew the lease of everything actually on screen. NOT gated on
           img.src: something whose first picture has not landed yet still
           holds a live session, and letting that get reaped is how a slow
           camera used to lose the slot it had just been given. */
        var lease = _cvxTiles[img.dataset.ip];
        if (lease && lease.sid) sids.push(lease.sid);
      }
    }
    /* Spend the beat's budget as a ROTATION, not on a prefix.
       This loop used to walk the tiles in DOM order and stop at six. A
       CV-X tile survived that, because only its first dial costs a slot and
       every frame after it is a free loopback read - but a MATROX tile pays
       a slot for every frame it ever fetches, so the same handful at the top
       of the list won the budget every single beat and the rest were never
       asked for a picture at all. Twelve fed, whatever the wall's size:
       with 56 cameras in the library, 44 of them could never paint.
       And a tile that is never ASKED never fails either - no error, no 8s
       timeout, so dark() never runs, .cam-off is never set, and the CSS
       keeps the note hidden. The result was a silent black rectangle that
       looked exactly like a broken camera while the camera was fine. That
       is the honesty rule inverted, and it sent techs to the wrong line.
       So: something that has NEVER painted goes first, wherever it sits (a
       region just scrolled into view fills in on the next beat instead of
       waiting out a lap), and the refresh rotation then resumes where the
       last beat stopped, so everything on screen gets its turn. */
    for (var f = 0; f < showing.length && kicked < CAM_MAX_LOADS; f++) {
      var ft = showing[f];
      if (ft._camShown || now < ft._camDue) continue;
      if (ft._camLoad()) kicked++;
    }
    for (var k = 0; k < showing.length && kicked < CAM_MAX_LOADS; k++) {
      var idx = (_cursor + k) % showing.length;
      var rt = showing[idx];
      if (!rt._camShown || now < rt._camDue) continue;
      if (rt._camLoad()) { kicked++; _cursor = (idx + 1) % showing.length; }
    }
    /* one lease-renewal per pass for everything actually on screen. Python
       answers with liveness AND the session's frame count, which is what lets
       a quiet controller be told from a dark one. */
    if (sids.length) {
      BV.api.call("cvx_tile_sync", sids, viewer()).then(function (alive) {
        Object.keys(_cvxTiles).forEach(function (tip) {
          var l = _cvxTiles[tip];
          if (!l || !l.sid || !alive[l.sid]) return;
          var im = byIp[tip];
          if (alive[l.sid].alive === false) {
            /* the session died under us: drop the lease so it redials on its
               own backoff instead of re-asking a sid that is gone */
            delete _cvxTiles[tip];
            if (im) {
              im.removeAttribute("src");
              im._camDue = Date.now() + CAM_REFRESH_MS;
              if (im._camSay) im._camSay(NOTE.dark);
              im._camDarkNow = 1;
              if (im._camOnState) im._camOnState("dark");
            }
            return;
          }
          /* alive: a session that has never pushed a picture is a quiet
             camera, not an absent one — say the true thing if it goes dark */
          if (im && im._camSay) {
            im._camSay(alive[l.sid].frames ? NOTE.dark : NOTE.quiet);
          }
        });
      }).catch(function () {});
    }
  }

  function start() {
    if (_timer) return;
    _timer = setInterval(pass, CAM_REFRESH_MS);
    setTimeout(pass, 150);   /* first pictures light up now-ish, not a beat later */
  }

  BV.camFeed = {
    REFRESH_MS: CAM_REFRESH_MS,
    MAX_LOADS: CAM_MAX_LOADS,   /* new plant fetches per beat - the wall's real ceiling */
    NOTE: NOTE,
    url: camLiveUrl,
    attach: attach,
    detach: detach,
    resume: resume,
    register: register,
    unregister: unregister,
    release: release,
    lease: function (ip) { return _cvxTiles[ip] || null; },
    take: take,
    give: give,
    paused: paused,
    /* the probe drives one beat by hand rather than waiting out the interval */
    _pass: pass,
  };
})();
