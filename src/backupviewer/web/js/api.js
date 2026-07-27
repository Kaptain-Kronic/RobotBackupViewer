/* api.js - promise wrapper over window.pywebview.api with the {ok,data,error} envelope.
   Degrades gracefully when opened in a plain browser (no bridge): BV.api.bridged=false. */
(function () {
  "use strict";

  /* solo mode: a popped-out backup window boots pinned to ONE session. The
     sid rides the URL FRAGMENT (#sid=..., stamped by pop_out_backup -
     WebView2 refuses file:// urls with a QUERY string outright); every
     content call then names that session via the SID_POS injection below.
     The router replaces the hash with a real route right after boot. */
  var soloSid = null;
  try {
    var mq = /[?&]sid=([^&]+)/.exec(location.search) || /^#sid=(.+)$/.exec(location.hash);
    if (mq) soloSid = decodeURIComponent(mq[1]);
  } catch (e) { /* malformed marker - boot as the main window */ }
  BV.solo = !!soloSid;
  BV.soloSid = soloSid;
  if (BV.solo) document.body.classList.add("solo");

  /* cvx-window mode: a CV-X remote popped into its own window (cvx_remote_window
     stamps #cvx=<session>&label=<label>). It boots straight into the remote
     overlay and shows no app chrome - deliberately NOT `solo`, which pins a
     BACKUP session and would inject its sid into content calls. */
  BV.cvxWin = null;
  try {
    var cm = /^#cvx=([^&]+)(?:&label=([^&]*))?$/.exec(location.hash);
    if (cm) BV.cvxWin = { sid: decodeURIComponent(cm[1]),
                          label: cm[2] ? decodeURIComponent(cm[2]) : "" };
  } catch (e2) { /* malformed marker - boot as the main window */ }
  if (BV.cvxWin) document.body.classList.add("cvxwin");

  /* which window is this? - the key viewfinder_start mirrors (null = the main
     app window). Every phone button asks THIS, so the phone shows the window
     the button was pressed in. */
  BV.windowKey = function () {
    return (BV.cvxWin && BV.cvxWin.sid) || BV.soloSid || null;
  };

  /* method -> the 0-based argument position of `sid` (the slot IMMEDIATELY
     before a trailing `side`, or last when there is no side). Solo windows
     inject their pinned sid here. Drift between this table and api.py's
     signatures fails LOUD (the arg-count assert below + the pytest
     signature guard) instead of silently reading the wrong session; Python
     folds None back to each optional's default where we pad over one. */
  var SID_POS = {
    get_frames: 0, get_io: 0, get_registers: 1, get_programs: 0,
    get_program_variables: 1, get_macros: 0, get_dcs_files: 0, get_dcs: 1,
    get_dcs_zones: 0, get_robot_pose: 0, get_sysvar_records: 0, get_sysvar: 1,
    get_mhvalves: 0, get_magnet: 0, get_payloads: 0, search_backup: 1,
    get_overview: 0, get_styles: 0, get_program: 1,
    get_call_tree: 2, get_alarm_files: 0, get_alarms: 4, list_files: 0,
    get_file: 1, get_photos: 0, get_image: 1,
    /* the ws_* edit-workspace endpoints are deliberately absent: they are
       path-addressed and take no sid, so a solo window needs no injection. */
  };

  var readyResolve;
  var ready = new Promise(function (resolve) { readyResolve = resolve; });

  function haveBridge() {
    return window.pywebview && window.pywebview.api;
  }

  if (haveBridge()) readyResolve(true);
  else {
    window.addEventListener("pywebviewready", function () { readyResolve(true); });
    /* plain-browser fallback: give the bridge 1.5s to appear, then mark unbridged */
    setTimeout(function () { if (!haveBridge()) readyResolve(false); }, 1500);
  }

  /* Slow synchronous endpoints: raise the global busy indicator while they run
     (the app is never frozen — pywebview runs each call on its own thread — it
     just never SAID it was working), and dedupe identical concurrent calls so a
     double-click can't run the same heavy work twice. */
  var SLOW = {
    lib_open: "opening backup…", open_backup: "opening backup…",
    open_compare: "opening comparison…", lib_apply_renames: "renaming folders…",
    lib_merge: "merging robots…", lib_relocate: "moving folders…",
    lib_list: "loading library…",  /* a changed tree makes this a full rescan */
    lib_rescan: "rescanning library…", lib_bulk_add: "adding robots…",
    lib_resolve_names: "reading names from backups…",  /* fix-names preview: opens every selected backup */
  };
  var inflight = {};   /* method + BV.KEYSEP + argsJSON -> the pending promise */

  BV.api = {
    bridged: null, /* unknown until ready resolves */
    ready: ready.then(function (ok) { BV.api.bridged = ok; return ok; }),

    call: function (method) {
      var args = Array.prototype.slice.call(arguments, 1);
      if (BV.solo && Object.prototype.hasOwnProperty.call(SID_POS, method)) {
        var pos = SID_POS[method];
        if (args.length > pos) {
          /* the sid slot is occupied - the table and api.py disagree */
          throw new Error("SID_POS drift: " + method + " got " + args.length +
            " args but sid belongs at " + pos);
        }
        while (args.length < pos) args.push(null);
        args.push(BV.soloSid);
      }
      var slow = SLOW[method];
      var key = null;
      if (slow) {
        try { key = method + BV.KEYSEP + JSON.stringify(args); } catch (e) { key = null; }
        if (key && inflight[key]) return inflight[key];   /* double-click -> same promise */
      }
      var p = BV.api.ready.then(function (ok) {
        if (!ok) {
          var e = new Error("Not running inside the app (no pywebview bridge)");
          e.code = "NO_BRIDGE";
          throw e;
        }
        var fn = window.pywebview.api[method];
        if (!fn) {
          var e2 = new Error("Unknown api method: " + method);
          e2.code = "NO_METHOD";
          throw e2;
        }
        return fn.apply(window.pywebview.api, args).then(function (res) {
          if (!res || res.ok !== true) {
            var err = (res && res.error) || { code: "UNKNOWN", message: "unknown error" };
            var ex = new Error(err.message);
            ex.code = err.code;
            throw ex;
          }
          return res.data;
        });
      });
      if (slow) {
        if (BV.jobs) BV.jobs.busy(slow);
        var settle = function () {
          if (key) delete inflight[key];
          if (BV.jobs) BV.jobs.done();
        };
        p = p.then(function (v) { settle(); return v; },
                   function (e) { settle(); throw e; });
        if (key) inflight[key] = p;
      }
      return p;
    },
  };
})();
