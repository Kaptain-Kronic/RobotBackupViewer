/* router.js - hash router + app boot. Loaded last. */
(function () {
  "use strict";

  var tabbar = document.getElementById("tabbar");
  var view = document.getElementById("view");
  var toolbar = document.getElementById("toolbar");
  var statusL = document.getElementById("status-left");
  var statusR = document.getElementById("status-right");

  /* tabs flex-wrap to a second row on skinny windows (no scroll strip) */

  /* expose the content area's visible height as --view-h so sticky elements
     (overview sidebar) can fit themselves to it at any font/scale */
  new ResizeObserver(function () {
    document.documentElement.style.setProperty("--view-h", view.clientHeight + "px");
  }).observe(view);

  BV.tabEnabled = function (tab) {
    var m = BV.state.manifest;
    if (!m) return false;
    if (tab.always) return true;
    return !!(m.tabs && m.tabs[tab.id]);
  };

  function buildTabbar() {
    tabbar.innerHTML = "";
    /* shell screens (home/backup) own the whole window - no viewer tabs in the
       bar until a robot is actually open */
    if (!BV.state.manifest) return;
    var n = 0;
    BV.tabs.forEach(function (tab) {
      if (tab.hidden) return;
      var enabled = BV.tabEnabled(tab);
      if (enabled) n++;
      var b = BV.el("button", { class: "tab-btn", id: "tab-" + tab.id },
        (enabled ? '<span class="tab-num">' + n + "</span>" : "") + BV.esc(tab.label));
      b.disabled = !enabled;
      if (!enabled) b.title = "not available in this backup";
      b.addEventListener("click", function () { location.hash = "#" + tab.id; });
      tabbar.appendChild(b);
    });
  }

  function setActive(tabId) {
    tabbar.querySelectorAll(".tab-btn").forEach(function (b) {
      b.classList.toggle("active", b.id === "tab-" + tabId);
    });
  }

  function rightStatusHtml() {
    var v = BV.state.version ? "ver. " + BV.state.version.split(".").slice(0, 2).join(".") : "";
    return v + ' <span class="credit">cody beach+claude code</span>';
  }

  function updateStatus() {
    var m = BV.state.manifest;
    if (!m) {
      statusL.innerHTML = '<span class="dim">no backup open</span>';
      statusR.innerHTML = rightStatusHtml();
      return;
    }
    statusL.innerHTML =
      '<span class="accent">' + BV.esc(m.robot_name || m.name) + "</span>" +
      (m.f_number ? '<span class="sep">·</span>' + BV.esc(m.f_number) : "") +
      '<span class="sep">·</span>' + m.file_count + " files" +
      '<span class="sep">·</span><span title="' + BV.esc(m.path) + '">' + BV.esc(m.name) + "</span>" +
      (m.backup_type && m.backup_type !== "unknown"
        ? ' <span class="pill ghost">' + BV.esc(m.backup_type) + "</span>" : "") +
      (m.truncated_scan ? ' <span class="pill warn">scan truncated</span>' : "") +
      (BV.state.compare
        ? '<span class="sep">·</span><a href="#compare">vs ' +
          BV.esc(BV.state.compare.robot_name || BV.state.compare.name) + "</a>"
        : "");
    statusR.innerHTML = rightStatusHtml();
  }

  function emptyState() {
    toolbar.innerHTML = "";
    toolbar.classList.add("hidden");
    view.classList.remove("no-pad");
    var inApp = BV.api.bridged;
    view.innerHTML =
      '<div class="empty-state">' +
      '<div class="big">no backup open</div>' +
      (inApp
        ? '<button class="btn primary" id="es-open">open backup folder</button>' +
          '<div class="hint">or press <kbd>ctrl</kbd>+<kbd>o</kbd></div>'
        : '<div class="hint">this page is running outside the app shell — launch via <code>python run.py</code></div>') +
      "</div>";
    var btn = document.getElementById("es-open");
    if (btn) btn.addEventListener("click", BV.openBackupFlow);
  }

  /* toolbar shows/hides itself based on content - tabs build it asynchronously,
     so visibility can never be decided at route time */
  new MutationObserver(function () {
    toolbar.classList.toggle("hidden", !toolbar.firstElementChild ||
      !toolbar.firstElementChild.childElementCount);
  }).observe(toolbar, { childList: true, subtree: true });

  function isShell(tab) { return !!(tab && tab.shell); }

  function route() {
    BV.currentVTable = null;
    BV.currentSearch = null;

    /* running outside the app shell (plain browser, no python bridge): keep the
       old "launch via run.py" hint instead of a home screen that can't load */
    if (!BV.api.bridged) { buildTabbar(); setActive(null); updateStatus(); emptyState(); return; }

    var hash = location.hash.slice(1);
    var parts = hash.split("/");
    var tabId = parts[0] || (BV.state.manifest ? "overview" : "home");
    var tab = BV.tabs.find(function (t) { return t.id === tabId; });

    buildTabbar();

    if (!BV.state.manifest) {
      /* no robot open: only shell screens are reachable, everything else -> home */
      if (!isShell(tab)) {
        if (location.hash !== "#home") { location.hash = "#home"; return; }
        tab = BV.tabs.find(function (t) { return t.id === "home"; });
      }
    } else if (!isShell(tab) && (!tab || !BV.tabEnabled(tab))) {
      tab = BV.tabs.find(BV.tabEnabled) || BV.tabs[BV.tabs.length - 1];
      if (("#" + tab.id) !== location.hash) { location.hash = "#" + tab.id; return; }
    }
    setActive(tab.id);
    view.classList.remove("no-pad");
    view.scrollTop = 0;
    /* each route renders into fresh slots: a stale async render from a previous
       route appends into a detached node and can never duplicate content */
    toolbar.innerHTML = "";
    var tslot = BV.el("div", { class: "toolbar-slot" });
    toolbar.appendChild(tslot);
    view.innerHTML = "";
    var slot = BV.el("div", { class: "view-slot" });
    view.appendChild(slot);
    tab.render(slot, tslot, parts.slice(1));
    updateStatus();
  }

  BV.openBackupFlow = function () {
    BV.api.call("pick_backup_folder").then(function (path) {
      if (!path) return;
      return BV.api.call("open_backup", path).then(function (manifest) {
        BV.state.setManifest(manifest);
        buildTabbar();
        BV.toast(manifest.robot_name ? manifest.robot_name + " · " + manifest.file_count + " files" : "backup opened");
        if (location.hash !== "#overview") location.hash = "#overview";
        else route();
      });
    }).catch(function (e) {
      BV.toast(e.message);
    });
  };

  BV.goHome = function () {
    if (location.hash === "#home") route();   /* same hash fires no hashchange */
    else location.hash = "#home";
  };

  /* the logo doubles as the home button (browser convention) */
  var logo = document.getElementById("logo");
  if (logo) {
    logo.classList.add("clickable");
    logo.title = "home";
    logo.addEventListener("click", BV.goHome);
  }

  document.getElementById("btn-open").addEventListener("click", BV.openBackupFlow);
  document.getElementById("btn-compare").addEventListener("click", function () {
    if (!BV.state.manifest) { BV.toast("open a backup first"); return; }
    if (BV.state.compare) location.hash = "#compare";
    else BV.compareFlow();
  });
  document.getElementById("btn-theme").addEventListener("click", function () { BV.theme.picker(); });
  document.getElementById("btn-help").addEventListener("click", function () { BV.helpOverlay(); });
  document.getElementById("btn-settings").addEventListener("click", function () { BV.uiPrefs.modal(); });

  /* global backup-wide search */
  var gsInput = document.querySelector("#global-search input");
  BV.focusGlobalSearch = function () { gsInput.focus(); gsInput.select(); };
  document.getElementById("global-search").addEventListener("click", function (e) {
    if (e.target !== gsInput) gsInput.focus();
  });
  gsInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && gsInput.value.trim()) {
      var target = "#search/" + encodeURIComponent(gsInput.value.trim());
      if (location.hash === target) route(); /* same hash fires no hashchange - re-route by hand */
      else location.hash = target;
      gsInput.blur();
      e.preventDefault();
      e.stopPropagation();
    } else if (e.key === "Escape") {
      gsInput.value = "";
      gsInput.blur();
      e.stopPropagation();
    }
  });

  window.addEventListener("hashchange", route);

  /* font/scale changes re-render the active tab so virtual tables pick up
     the new row height and em column widths */
  BV.state.on("uiprefs", function () {
    if (BV.state.manifest) route();
  });

  /* ---- boot ---- */
  BV.api.ready.then(function (bridged) {
    if (!bridged) { buildTabbar(); emptyState(); updateStatus(); return; }
    BV.api.call("get_version").then(function (v) {
      BV.state.version = v;
      updateStatus();
    }).catch(function () {});
    BV.theme.load().catch(function () {}).then(function () {
      return BV.api.call("get_settings");
    }).then(function (settings) {
      BV.state.settings = settings || {};
      BV.uiPrefs.apply(BV.state.settings);
      return BV.api.call("get_state");
    }).then(function (manifest) {
      if (manifest) BV.state.setManifest(manifest);
      buildTabbar();
      /* with a backup passed at startup, land in its viewer; otherwise the home
         menu. a deep-link hash (other than #home) is honoured when a backup is open. */
      var want = manifest
        ? ((location.hash && location.hash !== "#home") ? location.hash : "#overview")
        : (location.hash || "#home");
      if (location.hash === want) route();   /* same hash fires no hashchange - route by hand */
      else location.hash = want;             /* hashchange -> route() */
    }).catch(function () {
      route();
    });
  });
})();
