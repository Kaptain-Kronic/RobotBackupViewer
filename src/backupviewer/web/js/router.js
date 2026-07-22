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

  /* the positional key badges follow the keyboard's number row: 1-9, then -
     and = (0 stays pinned to the 3d view). Shared with keys.js so the badge on
     a tab and the key that opens it can never drift apart. */
  BV.tabKeyBadge = function (i) {   /* i = 0-based position among positional tabs */
    if (i < 9) return String(i + 1);
    if (i === 9) return "-";
    if (i === 10) return "=";
    return "";                      /* out of keys: tab is click-only */
  };
  BV.positionalTabs = function () {
    return BV.tabs.filter(function (t) {
      return !t.hidden && t.id !== "view3d" && BV.tabEnabled(t);
    });
  };

  function buildTabbar() {
    tabbar.innerHTML = "";
    /* shell screens (home/backup) own the whole window - no viewer tabs in the
       bar until a robot is actually open */
    if (!BV.state.manifest) return;
    /* display order = the keyboard's number row, exactly: 1..9, then 0 (the
       pinned 3d view), then - and =. With ten-plus positional tabs the 3d tab
       moves in FRONT of the tenth so its 0 badge sits between 9 and -; with
       nine or fewer it stays at the end, right after the last digit. */
    var tabs = BV.tabs.filter(function (t) { return !t.hidden; });
    var v3 = tabs.find(function (t) { return t.id === "view3d"; });
    if (v3) {
      tabs = tabs.filter(function (t) { return t !== v3; });
      var at = tabs.length, seen = 0;
      for (var i = 0; i < tabs.length; i++) {
        if (BV.tabEnabled(tabs[i])) {
          if (++seen === 9) { at = i + 1; break; }
        }
      }
      tabs.splice(at, 0, v3);
    }
    var n = 0;
    tabs.forEach(function (tab) {
      var enabled = BV.tabEnabled(tab);
      /* the 3d view is pinned to the 0 key, so its badge shows 0 and it
         never consumes a positional number-row slot */
      var badge = "";
      if (enabled) badge = tab.id === "view3d" ? "0" : BV.tabKeyBadge(n++);
      var b = BV.el("button", { class: "tab-btn", id: "tab-" + tab.id },
        (badge ? '<span class="tab-num">' + badge + "</span>" : "") + BV.esc(tab.label));
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

  /* the credit is a clickable pill: it's the app's only "who made this / how do
     I reach you" affordance, so it has to LOOK clickable without shouting over
     the status line. Delegated below (statusR is innerHTML-rebuilt constantly,
     so a per-render listener would leak). */
  var CONTACT = "cmbeach96+backupviewer@gmail.com";
  var REPO = "https://github.com/Kaptain-Kronic/RobotBackupViewer";

  function rightStatusHtml() {
    var v = BV.state.version ? "ver. " + BV.state.version.split(".").slice(0, 2).join(".") : "";
    return v + ' <span class="pill ghost credit-pill" title="about + contact">' +
      "cody beach+claude code</span>";
  }

  function aboutModal() {
    var body = BV.el("div", { class: "about-box" });
    body.appendChild(BV.el("div", { class: "about-line" },
      "backupviewer <span class=\"accent\">" +
      BV.esc(BV.state.version || "") + "</span>"));
    body.appendChild(BV.el("div", { class: "about-line dim" }, "cody beach + claude code"));
    body.appendChild(BV.el("div", { class: "about-lbl" }, "questions, bugs, suggestions"));
    var mail = BV.el("div", { class: "about-mail" }, BV.esc(CONTACT));
    body.appendChild(mail);
    var acts = BV.el("div", { class: "lf-actions" });
    var copyBtn = BV.el("button", { class: "btn primary", title: "copy the address" },
      "copy email");
    copyBtn.addEventListener("click", function () { BV.copyText(CONTACT, "email copied"); });
    var repoBtn = BV.el("button", { class: "btn", title: "open the source on GitHub" },
      "source");
    repoBtn.addEventListener("click", function () {
      BV.api.call("open_url", REPO).catch(function () { BV.copyText(REPO, "link copied"); });
    });
    var closeBtn = BV.el("button", { class: "btn" }, "close");
    acts.appendChild(copyBtn);
    acts.appendChild(repoBtn);
    acts.appendChild(closeBtn);
    body.appendChild(acts);
    var m = BV.modal("about", body);
    closeBtn.addEventListener("click", function () { m.close(); });
  }

  /* ONE delegated listener for the life of the app */
  document.getElementById("statusbar").addEventListener("click", function (e) {
    if (e.target.closest(".credit-pill")) aboutModal();
  });

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

  /* the search box + compare + the 1-9 tabbar are backup-viewer chrome - hide
     them on the shell (home/backup) screens so the main menu's topbar is just
     logo·⚙·?. buildTabbar() keys off "manifest present", which stays true
     once a robot is open, so the tabbar must be hidden here by ROUTE instead. */
  function setTopbarChrome(shell) {
    var s = document.getElementById("global-search");
    var c = document.getElementById("btn-compare");
    if (s) s.classList.toggle("hidden", shell);
    if (c) c.classList.toggle("hidden", shell);
    if (tabbar) tabbar.classList.toggle("hidden", shell);
  }

  function route() {
    BV.currentVTable = null;
    BV.currentSearch = null;

    /* running outside the app shell (plain browser, no python bridge): keep the
       old "launch via run.py" hint instead of a home screen that can't load */
    if (!BV.api.bridged) { buildTabbar(); setActive(null); updateStatus(); setTopbarChrome(true); emptyState(); return; }

    var hash = location.hash.slice(1);
    var parts = hash.split("/");
    var tabId = parts[0] || (BV.state.manifest ? "overview" : "home");
    var tab = BV.tabs.find(function (t) { return t.id === tabId; });

    buildTabbar();

    /* router-initiated redirects REPLACE the current history entry instead of
       pushing one. A pushed redirect turns history.back() into a trap: on a
       camera-only backup #overview isn't enabled, so back landed on #overview,
       which instantly pushed #photos again — the screen flashed and never left */
    if (!BV.state.manifest) {
      /* no robot open: only shell screens are reachable, everything else -> home */
      if (!isShell(tab)) {
        if (location.hash !== "#home") { location.replace("#home"); return; }
        tab = BV.tabs.find(function (t) { return t.id === "home"; });
      }
    } else if (!isShell(tab) && (!tab || !BV.tabEnabled(tab))) {
      tab = BV.tabs.find(BV.tabEnabled) || BV.tabs[BV.tabs.length - 1];
      if (("#" + tab.id) !== location.hash) { location.replace("#" + tab.id); return; }
    }
    setActive(tab.id);
    setTopbarChrome(isShell(tab));
    view.classList.remove("no-pad");
    /* drop any persist-scroll ownership before resetting: the scroll-to-0 below
       fires a scroll event, and without this the OUTGOING tab's key would catch
       it and overwrite its own saved position with 0 (BV.persistScroll) */
    view._bvScrollKey = null;
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

  document.getElementById("btn-compare").addEventListener("click", function () {
    if (!BV.state.manifest) { BV.toast("open a backup first"); return; }
    if (BV.state.compare) location.hash = "#compare";
    else BV.compareFlow();
  });
  document.getElementById("btn-theme").addEventListener("click", function () { BV.themeUI.open(); });
  document.getElementById("btn-cog").addEventListener("click", function () { BV.uiPrefs.modal(); });
  document.getElementById("btn-help").addEventListener("click", function () { BV.helpOverlay(); });

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
  BV.route = route;   /* re-render the active tab in place (e.g. after switching dated backup) */

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
