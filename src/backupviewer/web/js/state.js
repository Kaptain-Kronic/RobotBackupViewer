/* state.js - app state: manifest of the open backup, per-tab data cache, pub/sub. */
(function () {
  "use strict";

  var listeners = {};

  BV.state = {
    manifest: null,        /* {path, name, file_count, robot_name, f_number, tabs:{}} */
    tabData: {},           /* tabId -> parsed payload (frontend cache) */
    settings: {},

    on: function (evt, fn) {
      (listeners[evt] = listeners[evt] || []).push(fn);
    },
    emit: function (evt, payload) {
      (listeners[evt] || []).forEach(function (fn) { fn(payload); });
    },

    setManifest: function (m) {
      BV.state.manifest = m;
      BV.state.tabData = {};
      BV.state.emit("manifest", m);
    },
  };
})();
