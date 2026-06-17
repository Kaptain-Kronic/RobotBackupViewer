/* api.js - promise wrapper over window.pywebview.api with the {ok,data,error} envelope.
   Degrades gracefully when opened in a plain browser (no bridge): BV.api.bridged=false. */
(function () {
  "use strict";

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

  BV.api = {
    bridged: null, /* unknown until ready resolves */
    ready: ready.then(function (ok) { BV.api.bridged = ok; return ok; }),

    call: function (method) {
      var args = Array.prototype.slice.call(arguments, 1);
      return BV.api.ready.then(function (ok) {
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
          if (typeof res.ms === "number") BV.api.lastMs = res.ms;
          return res.data;
        });
      });
    },
  };
})();
