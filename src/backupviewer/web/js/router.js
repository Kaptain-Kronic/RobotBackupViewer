/* router.js - hash router + app boot. Loaded last. */
(function () {
  "use strict";

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

  /* ---- the screens button ----
     ONE dropdown stands in for the old tab strip: the strip could never
     share the top row with chips + search (it alone outgrew the window),
     and it put screen NAVIGATION under the screen CONTROLS. The button
     names where you are ("5 · programs"), the menu is the map - same
     number badges, same disabled-dim honesty for tabs this backup lacks -
     and the number-row keys stay the fast path (keys.js reads the same
     list, so key and badge can never drift). */
  var screensBtn = document.getElementById("screens-btn");

  /* display order = the keyboard's number row, exactly: 1..9, then 0 (the
     pinned 3d view), then - and =. With ten-plus positional tabs the 3d tab
     moves in FRONT of the tenth so its 0 badge sits between 9 and -; with
     nine or fewer it stays at the end, right after the last digit. */
  function screenList() {
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
    return tabs.map(function (tab) {
      var enabled = BV.tabEnabled(tab);
      /* the 3d view is pinned to the 0 key, so its badge shows 0 and it
         never consumes a positional number-row slot */
      var badge = "";
      if (enabled) badge = tab.id === "view3d" ? "0" : BV.tabKeyBadge(n++);
      return { tab: tab, badge: badge, enabled: enabled };
    });
  }

  function syncScreens(tabId) {
    if (!BV.state.manifest || !tabId) { screensBtn.innerHTML = ""; return; }
    var cur = null;
    screenList().forEach(function (e) { if (e.tab.id === tabId) cur = e; });
    screensBtn.innerHTML =
      (cur && cur.badge ? '<span class="tab-num">' + cur.badge + "</span>" : "") +
      BV.esc(cur ? cur.tab.label : "screens") +
      '<span class="scr-caret">▾</span>';
  }

  screensBtn.addEventListener("click", function () {
    var curId = location.hash.slice(1).split("/")[0];
    BV.menu(screensBtn, screenList().map(function (e) {
      return {
        label: (e.badge ? e.badge + " · " : "") + e.tab.label,
        disabled: !e.enabled,
        title: e.enabled ? "" : "not available in this backup",
        active: e.tab.id === curId,
        onClick: function () { location.hash = "#" + e.tab.id; },
      };
    }));
  });

  /* the credit is a clickable pill: it's the app's only "who made this / how do
     I reach you" affordance, so it has to LOOK clickable without shouting over
     the status line. Delegated below (statusR is innerHTML-rebuilt constantly,
     so a per-render listener would leak). */
  var CONTACT = "cmbeach96+backupviewer@gmail.com";
  var REPO = "https://github.com/Kaptain-Kronic/RobotBackupViewer";

  function rightStatusHtml() {
    var v = BV.state.version ? "ver. " + BV.state.version.split(".").slice(0, 2).join(".") : "";
    var upd = BV.update ? BV.update.pillHtml() : "";
    return upd + v + ' <span class="pill ghost credit-pill" title="about + contact">' +
      "cody beach+claude code</span>";
  }

  function aboutModal() {
    var body = BV.el("div", { class: "about-box" });
    body.appendChild(BV.el("div", { class: "about-line" },
      "backupviewer <span class=\"accent\">" +
      BV.esc(BV.state.version || "") + "</span>"));
    body.appendChild(BV.el("div", { class: "about-line dim" }, "cody beach + claude code"));
    if (BV.update) body.appendChild(BV.update.aboutSection());
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
    if (e.target.closest(".update-pill")) aboutModal();
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

  /* ---- chrome edges ----
     The chrome sits IN FLOW above #view (content-scroll-under retired: rows
     ghosting under glass cost three rounds of bugs and ate the scrollbar,
     while the part that made the look - the background effect through the
     frost - never needed it). What remains is honest edges: a hairline per
     edge only while content is actually clipped there, and a sticky header
     pinned against the chrome supplies the panel's one line itself. */
  var chromeTop = document.getElementById("chrome-top");
  function underChrome() {
    var st = view.scrollTop;
    document.body.classList.toggle("under-top", st > 1);
    document.body.classList.toggle("under-bottom",
      st + view.clientHeight < view.scrollHeight - 1);
    /* builders REGISTER the fusing head (BV.chrome.fusedHead) - a per-scroll
       querySelector against a plant-scale tree was real milliseconds */
    var head = BV.chrome.fusedHead;
    var fused = false;
    if (head && head.isConnected) {
      var r = head.getBoundingClientRect();
      var topH = chromeTop.offsetHeight;
      fused = r.top <= topH + 2 && r.bottom > topH;
    }
    document.body.classList.toggle("chrome-fused", fused);
  }
  BV.chrome = {
    fusedHead: null,   /* the screen's chrome-fusing sticky header, if any */
  };
  view.addEventListener("scroll", underChrome, { passive: true });
  window.addEventListener("resize", underChrome);
  underChrome();

  function isShell(tab) { return !!(tab && tab.shell); }

  /* the search box + the screens button are backup-viewer chrome - hide them
     on the shell (home/cam/edit) screens. "manifest present" stays true once
     a robot is open, so they must be hidden here by ROUTE instead. */
  function setTopbarChrome(shell) {
    var s = document.getElementById("global-search");
    if (s) s.classList.toggle("hidden", shell);
    if (screensBtn) screensBtn.classList.toggle("hidden", shell);
    /* the SESSION tab strip stays through shell screens (browser behavior -
       home is just a screen); it only un-highlights there */
    if (BV.session) BV.session.setShell(shell);
  }

  function route() {
    BV.currentVTable = null;
    BV.currentSearch = null;

    /* running outside the app shell (plain browser, no python bridge): keep the
       old "launch via run.py" hint instead of a home screen that can't load */
    if (!BV.api.bridged) { syncScreens(null); updateStatus(); setTopbarChrome(true); emptyState(); return; }

    var hash = location.hash.slice(1);
    var parts = hash.split("/");
    var tabId = parts[0] || (BV.state.manifest ? "overview" : "home");
    var tab = BV.tabs.find(function (t) { return t.id === tabId; });

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
    } else if (BV.solo && isShell(tab)) {
      /* a solo pop-out has no home/library - shell routes bounce to the
         backup (the pass below redirects again if overview is disabled) */
      location.replace("#overview");
      return;
    } else if (!isShell(tab) && (!tab || !BV.tabEnabled(tab))) {
      /* land on a tab you could have CLICKED. The hidden always-on tabs
         (search/compare/pdiff/home/edit) are all "enabled", so a plain
         find(tabEnabled) picks whichever of them registers earliest - which is
         how a camera-only backup, whose overview is disabled, started opening
         the edit workspace instead of its photos. */
      tab = BV.tabs.find(function (t) { return !t.hidden && BV.tabEnabled(t); })
        || BV.tabs[BV.tabs.length - 1];
      if (("#" + tab.id) !== location.hash) { location.replace("#" + tab.id); return; }
    }
    syncScreens(isShell(tab) ? null : tab.id);
    setTopbarChrome(isShell(tab));
    /* every redirect path above returns, so the hash and tab.id agree by here */
    syncCubes();
    /* remember where this backup's tab is, so switching back lands there */
    if (!isShell(tab) && BV.session) BV.session.noteHash(location.hash);
    view.classList.remove("no-pad");
    BV.chrome.fusedHead = null;   /* the incoming screen registers its own */
    /* a shell screen may have mounted its own filter box in the topbar
       search slot - it dies with the screen */
    [].forEach.call(document.querySelectorAll("#topbar-search .screen-search"),
      function (n) { n.remove(); });
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
    underChrome();   /* fresh screen, fresh edge state */
  }

  BV.openBackupFlow = function () {
    BV.api.call("pick_backup_folder").then(function (path) {
      if (!path) return;
      return BV.api.call("open_backup", path).then(function (manifest) {
        BV.session.open(manifest);
        BV.state.setManifest(manifest);
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

  /* (compare moved into the overview toolbar - overview.js) */

  /* ---- the three cubes: library · multi-cam · workspace ----
     These are the app's navigation, in the slot the wordmark used to hold (it
     doubled as the home button). Book and camera are two LENSES on one screen,
     not two screens. The workspace spans robots, so its entry point belongs
     here and NOT in #topbar-right, where sitting between search and compare
     made a screen that outlives every backup read as backup chrome. */
  var cubes = {
    lib: document.getElementById("cube-lib"),
    cam: document.getElementById("cube-cam"),
    edit: document.getElementById("cube-edit"),
  };

  function syncCubes() {
    var hash = location.hash.slice(1).split("/")[0] || "";
    var onHome = !hash || hash === "home";
    var cam = !!(BV.home && BV.home.viewMode() === "multicam");
    if (cubes.lib) cubes.lib.classList.toggle("active", onHome && !cam);
    if (cubes.cam) cubes.cam.classList.toggle("active", onHome && cam);
    if (cubes.edit) cubes.edit.classList.toggle("active", hash === "edit");
  }
  BV.syncCubes = syncCubes;

  /* set the lens FIRST, then route: buildLibraryHead() and renderTree() both
     read viewMode() live as they paint, so the other order gives a backup-lens
     paint followed by a multicam repaint. Already on home, setViewMode
     repaints in place (the cheap path) and no route is needed at all. */
  function goLibrary(mode) {
    var onHome = !location.hash || location.hash === "#" || location.hash === "#home";
    if (BV.home) BV.home.setViewMode(mode);
    if (!onHome) BV.goHome();
    syncCubes();
  }
  if (cubes.lib) cubes.lib.addEventListener("click", function () { goLibrary("backup"); });
  if (cubes.cam) cubes.cam.addEventListener("click", function () { goLibrary("multicam"); });
  if (cubes.edit) cubes.edit.addEventListener("click", function () { BV.openWorkspace(); });

  /* the workspace count rides the anvil. This used to write the button's
     innerHTML wholesale, which with an icon inside would erase it on the first
     paint - so it writes into its own span and touches nothing else. */
  if (cubes.edit) {
    var badge = cubes.edit.querySelector(".cube-badge");
    var paintBadge = function () {
      var n = BV.workspace ? BV.workspace.count() : 0;
      var d = BV.workspace ? BV.workspace.pendingCount() : 0;
      if (badge) {
        badge.textContent = n ? String(n) : "";
        badge.classList.toggle("hidden", !n);
        badge.classList.toggle("dirty", !!d);
      }
      cubes.edit.title = n
        ? n + " program" + (n === 1 ? "" : "s") + " in the edit workspace" +
          (d ? ", " + d + " edited" : "")
        : "the edit workspace — programs you are editing across robots";
    };
    BV.state.on("workspace", paintBadge);
    paintBadge();
  }

  /* the phone lives in the top bar so it reaches ANY screen, not just a camera
     remote: whatever this window shows, the phone in your hand shows too */
  document.getElementById("btn-phone").addEventListener("click", function () {
    BV.openViewfinder();
  });
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

  /* a found update re-renders the statusbar (the pill lives in rightStatusHtml) */
  BV.state.on("update", updateStatus);

  /* ---- boot ---- */
  BV.api.ready.then(function (bridged) {
    if (!bridged) { emptyState(); updateStatus(); return; }
    /* a popped-out CV-X window is nothing but the remote: no backup, no tabs,
       no routing - load the theme so the overlay is dressed, then adopt the
       session that already exists and fill the window with it */
    if (BV.cvxWin) {
      document.title = "CV-X remote · " + (BV.cvxWin.label || "");
      BV.theme.load().catch(function () {}).then(function () {
        return BV.api.call("get_settings").catch(function () { return null; });
      }).then(function (settings) {
        BV.state.settings = settings || {};
        BV.uiPrefs.apply(BV.state.settings);
        BV.openCvxRemote("", BV.cvxWin.label, { adopt: BV.cvxWin.sid, owned: true });
      });
      return;
    }
    BV.api.call("get_version").then(function (v) {
      BV.state.version = v;
      updateStatus();
    }).catch(function () {});
    BV.theme.load().catch(function () {}).then(function () {
      return BV.api.call("get_settings");
    }).then(function (settings) {
      BV.state.settings = settings || {};
      BV.uiPrefs.apply(BV.state.settings);
      /* the working set + any unsaved drafts persist in settings, so restore
         them before anything paints the badge or opens #edit */
      if (BV.workspace) BV.workspace.load();
      /* a solo pop-out boots pinned to its sid; the main window asks for
         whatever is active (or nothing) */
      return BV.solo ? BV.api.call("get_state", BV.soloSid) : BV.api.call("get_state");
    }).then(function (manifest) {
      if (manifest) {
        if (BV.solo) {
          document.title = manifest.robot_name || manifest.name || "backupviewer";
        } else {
          BV.session.open(manifest);   /* seed the strip (e.g. --backup startup) */
        }
        BV.state.setManifest(manifest);
      }
      /* with a backup passed at startup, land in its viewer; otherwise the home
         menu. a deep-link hash (other than #home) is honoured when a backup is open. */
      var want = BV.solo
        ? "#overview"
        : manifest
          ? ((location.hash && location.hash !== "#home") ? location.hash : "#overview")
          : (location.hash || "#home");
      if (location.hash === want) route();   /* same hash fires no hashchange - route by hand */
      else location.hash = want;             /* hashchange -> route() */
    }).catch(function () {
      route();
    });
  });
})();
