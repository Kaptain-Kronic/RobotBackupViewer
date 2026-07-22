/* tabs/home.js - the ecosystem main menu (#home). The whole screen is the saved
   robot library, grouped PLANT -> LINE -> ROBOT, with collapsible folders.
   "+ add robot" opens a context menu (from backup / manually). Each robot row has
   a selection checkbox + edit; clicking the row opens the robot's backup. Per-LINE
   controls (select-all / backup / trash) act on that line's selected robots, and
   "backup" pulls fresh FTP backups for all selected at once, showing live per-row
   progress. Marked shell:true so the router lets it render with no manifest. */
(function () {
  "use strict";

  var _libWrap = null;          /* the mounted library container, for in-place refresh */
  var _cl = BV.checklist({ onChange: syncToolbar });   /* selected robots (shared checklist) */
  var _robots = [];             /* last loaded library list, for client-side collision checks */
  var _lastAbsorbMsg = "";      /* absorption toast dedupe (same folders every rescan) */
  var _showHidden = false;      /* reveal hidden robots in the list */
  var _showHiddenBtn = null;    /* the header toggle (shown only when some are hidden) */
  var _warnedTruncated = false; /* the scan-cap warning toast fires once per session */
  var _visibleRobots = [];      /* the currently-rendered robots — the sticky toolbar's scope */
  var _sortMode = "";           /* name | ip | date; lazily read from settings (lib_sort) */
  var _filter = "";             /* library filter query, lowercased ("" = off) */
  var _filterBox = null;        /* the head's search box (match counter lives on it) */
  var _lastData = null;         /* last lib_list payload — filter re-renders without a refetch */
  var _liveTargets = null;      /* BV.jobs.activeTargets() snapshot, taken once per tree paint */
  var _camCounts = {};          /* robot id -> linked-camera count, built once per tree paint */

  var SORT_LABELS = { name: "name", ip: "IP", date: "last backup" };

  function sortMode() {
    if (!_sortMode) {
      _sortMode = ((BV.state.settings || {}).lib_sort) || "name";
      if (!SORT_LABELS[_sortMode]) _sortMode = "name";
    }
    return _sortMode;
  }

  function setSortMode(mode) {
    _sortMode = mode;
    if (BV.state.settings) BV.state.settings.lib_sort = mode;
    BV.api.call("set_setting", "lib_sort", mode).catch(function () {});
    var b = _libWrap && _libWrap.querySelector(".lib-sort");
    if (b) b.textContent = "sort: " + SORT_LABELS[mode];   /* head persists across refreshes */
    /* sorting is client-side ordering: re-render the cached listing. Hitting
       lib_list here forced a full rescan whenever the tree had changed — and
       DURING a mass backup the tree changes every second, so flipping the sort
       re-scanned mid-run and pilled every in-flight robot "partial". */
    rerenderFromCache();
  }

  function nameCmp(a, b) { return (a.robot || "").localeCompare(b.robot || ""); }

  function ipNum(r) {
    var m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(((r.ips && r.ips[0]) || "").trim());
    if (!m) return Infinity;                         /* no IP -> sinks last */
    return (+m[1]) * 16777216 + (+m[2]) * 65536 + (+m[3]) * 256 + (+m[4]);
  }

  function robotComparator() {
    var mode = sortMode();
    if (mode === "ip") {
      return function (a, b) {
        var ia = ipNum(a), ib = ipNum(b);
        if (ia !== ib) return ia < ib ? -1 : 1;      /* octet-numeric, not lexicographic */
        return nameCmp(a, b);
      };
    }
    if (mode === "date") {
      return function (a, b) {
        var da = a.last_backup || "", db = b.last_backup || "";
        if (da !== db) return da < db ? 1 : -1;      /* ISO strings: newest first, never-backed-up last */
        return nameCmp(a, b);
      };
    }
    return nameCmp;
  }

  /* the shared library tree renders the PLANT -> LINE folders (grouping, sort,
     collapse + filter semantics, skeleton notes live in components/libtree.js);
     home plugs in its rich robot rows and the per-line select-all boxes. The
     module-level instance keeps fold state across refreshes and remounts. */
  var _tree = BV.libTree({
    skeleton: true,
    row: function (r, nested) { return robotRow(r, nested); },
    /* line header extras = the select-all box only; action buttons live ONCE
       in the sticky library header (with 50+ lines, per-line rows ate the screen) */
    lineExtras: function (ln, lineRobots, groupKey) {
      var controls = BV.el("div", { class: "lib-line-controls" });
      controls.addEventListener("click", function (e) { e.stopPropagation(); });
      var sa = BV.el("input", { type: "checkbox", class: "lf-check lib-line-selall",
        title: "select all / clear line" });
      _cl.group(sa, function () {
        return lineRobots.map(function (r) { return r.id; });
      }, groupKey);
      controls.appendChild(sa);
      return controls;
    },
  });

  /* ---- screen ---- */

  function render(view, toolbar, params) {
    _libWrap = BV.el("div", { class: "home-library" });
    view.appendChild(_libWrap);
    loadLibrary();
    BV.currentSearch = _filterBox;   /* '/' focuses the library filter */
  }

  /* poll the backend's library-scan snapshot into `el` while a lib_list /
     lib_rescan call is in flight; returns stop(). The first look at a plant-
     scale tree is a full rescan (10s+), and a dead "loading…" reads as a
     crash - this shows the scan actually moving (done/total · current robot). */
  function watchScanProgress(el) {
    var iv = setInterval(function () {
      BV.api.call("lib_scan_progress").then(function (p) {
        if (!p || !p.active) return;   /* signature check / cache path: keep the quiet label */
        renderScanBar(el, {
          status: "scanning", scanned: p.done, total: p.total, current: p.current,
          found: p.total ? 0 : p.done, /* first-ever scan has no estimate: show a count instead */
        });
      }).catch(function () {});        /* no bridge / transient: stay quiet */
    }, 400);
    return function stop() { clearInterval(iv); };
  }

  function loadLibrary() {
    if (!_libWrap) return;
    var body = _libWrap.querySelector(".home-lib-body");
    var stopProgress = null;
    if (!body) {                                 /* first paint: build the shell once */
      _libWrap.innerHTML = "";
      _libWrap.appendChild(buildLibraryHead());
      body = BV.el("div", { class: "home-lib-body" });
      _libWrap.appendChild(body);
      var loading = BV.el("div", { class: "home-lib-loading" },
        '<div class="dim">checking library…</div>');
      body.appendChild(loading);
      stopProgress = watchScanProgress(loading);
    }

    BV.api.call("lib_list").then(function (data) {
      if (stopProgress) stopProgress();
      _robots = (data && data.robots) || [];
      _lastData = data;
      /* repaint IN PLACE: the old tree stays on screen until the new one is
         built, and the scroll position survives — refreshes (post-action or
         watcher-triggered) stop flashing and jumping back to the top */
      var scroller = document.getElementById("view");
      var keep = scroller ? scroller.scrollTop : 0;
      renderTree(body, data);
      if (scroller) scroller.scrollTop = keep;
      reattachProgress();   /* repaint any backups already running */
      if (data && data.scan_truncated && !_warnedTruncated) {
        _warnedTruncated = true;
        BV.toast("backup scan hit its safety cap — some backups may not be listed", 4000);
      }
      /* copied folders that carried a robot.json get folded into that robot's
         history by identity — SAY so, or the copy looks like it never arrived */
      if (data && data.scan_absorbed && data.scan_absorbed.length) {
        var absorbMsg = data.scan_absorbed.map(function (a) {
          return a.count + " copied snapshot" + (a.count === 1 ? "" : "s") +
            " joined " + (a.robot || "a robot");
        }).join(" · ");
        if (absorbMsg !== _lastAbsorbMsg) {
          _lastAbsorbMsg = absorbMsg;
          BV.toast(absorbMsg, 4200);
        }
      }
    }).catch(function (e) {
      if (stopProgress) stopProgress();
      body.innerHTML = '<div class="dim">library unavailable: ' + BV.esc(e.message) + "</div>";
    });
  }

  /* ONE sticky header row: title · selection actions (act on checked robots
     anywhere in the tree) · library actions. It stays pinned while the 50-line
     plants scroll — the per-line button rows are gone. Built once per mount;
     refreshes repaint only the body, so button state must self-maintain. */
  function buildLibraryHead() {
    var head = BV.el("div", { class: "home-lib-head" });
    head.appendChild(BV.el("h2", null, "library"));

    /* find a robot fast: filters by robot name / model / IP / note text, and a
       matching plant or line name keeps its whole group. The tree re-renders
       from the cached listing — no rescan. */
    _filterBox = BV.searchBox({
      placeholder: "filter robots…",
      onChange: function (q) {
        _filter = (q || "").toLowerCase();
        rerenderFromCache();
      },
    });
    _filterBox.input.value = _filter;    /* a remount keeps the active filter */
    head.appendChild(_filterBox.el);

    var selActs = BV.el("div", { class: "home-lib-selacts" });
    selActs.appendChild(BV.el("span", { class: "lib-sel-count dim" }, ""));
    var bk = BV.el("button", { class: "btn lib-act-backup", title: "back up the selected robots" },
      "backup");
    bk.addEventListener("click", function () { startLineBackup(_visibleRobots); });
    var hd = BV.el("button", { class: "btn lib-act-hide", title: "hide the selected robots from view" },
      "hide");
    hd.addEventListener("click", function () { hideSelectedInLine(_visibleRobots); });
    var sc = BV.el("button", { class: "btn lib-act-scan",
      title: "scan the selected robots' backups — DCS options, signatures, mastering, unused programs, or a find" },
      "scan");
    sc.addEventListener("click", function () {
      var sel = _visibleRobots.filter(function (r) { return _cl.has(r.id); });
      if (!sel.length) { BV.toast("select robots first"); return; }
      BV.scanUI.open(sel);
    });
    /* fix names / merge / move live INSIDE manage backups now (with the backup
       health panels) - the selection row stays backup / hide / scan / manage */
    var mb = BV.el("button", { class: "btn lib-act-manage",
      title: "backup health (last run, retries, partial + stale backups) and robot tidy-up (fix names, merge, move)" },
      "manage backups");
    mb.addEventListener("click", function () {
      if (BV.manageUI) BV.manageUI.open();
    });
    selActs.appendChild(bk);
    selActs.appendChild(hd);
    selActs.appendChild(sc);
    selActs.appendChild(mb);
    head.appendChild(selActs);

    var headActs = BV.el("div", { class: "home-lib-actions" });
    var sortBtn = BV.el("button", { class: "btn lib-sort", title: "library sort order" },
      "sort: " + SORT_LABELS[sortMode()]);
    sortBtn.addEventListener("click", function () {
      BV.menu(sortBtn, ["name", "ip", "date"].map(function (mode) {
        return { label: SORT_LABELS[mode], onClick: function () { setSortMode(mode); } };
      }));
    });
    var cancelAll = BV.el("button", { class: "btn lib-cancel-all hidden", id: "lib-cancel-all" },
      "cancel backups");
    cancelAll.addEventListener("click", cancelAllBackups);
    _showHiddenBtn = BV.el("button", { class: "btn lib-show-hidden hidden",
      title: "show robots you've hidden" }, "show hidden");
    _showHiddenBtn.addEventListener("click", function () { _showHidden = !_showHidden; refresh(); });
    /* "refresh library", not "rescan" - "scan" is the health-scan button now,
       and this one re-reads folders, it doesn't scan backup contents */
    var rescanBtn = BV.el("button", { class: "btn lib-rescan",
      title: "re-read the library folder from disk (picks up copied-in backups)" }, "refresh library");
    rescanBtn.addEventListener("click", function () {
      rescanBtn.disabled = true;
      /* the tree stays up during the rescan; a slim bar above it shows the
         scan actually moving (a plant-scale tree takes a while) */
      var body = _libWrap && _libWrap.querySelector(".home-lib-body");
      var strip = null;
      var stopProgress = null;
      if (body) {
        strip = BV.el("div", { class: "home-lib-loading" });
        body.insertBefore(strip, body.firstChild);
        stopProgress = watchScanProgress(strip);
      }
      function settle() {
        if (stopProgress) stopProgress();
        if (strip) strip.remove();
        rescanBtn.disabled = false;
      }
      BV.api.call("lib_rescan")
        .then(function () { settle(); BV.toast("library rescanned"); refresh(); })
        .catch(function (e) { settle(); BV.toast(e.message); });
    });
    /* (auto-link cameras lives in the manage-backups modal now, with the
       other library-tidy flows — BV.libActions.autoLink) */
    var addBtn = BV.el("button", { class: "btn lib-add-robot", id: "lib-add-robot",
      title: "add a robot to the library" }, "+ add robot");
    addBtn.addEventListener("click", function () {
      /* existing backups join the library by being COPIED into the library
         folder (Explorer) — the scan/watcher picks them up. Adding here is for
         robots that don't have backup data yet. */
      BV.menu(addBtn, [
        { label: "discover on network", onClick: discoverFlow },
        { label: "manually", onClick: function () { editRobotModal(null, true); } },
      ]);
    });
    headActs.appendChild(sortBtn);
    headActs.appendChild(cancelAll);
    headActs.appendChild(_showHiddenBtn);
    headActs.appendChild(rescanBtn);
    headActs.appendChild(addBtn);
    head.appendChild(headActs);
    return head;
  }

  /* the backend watcher saw the library folder change on disk (Explorer copy /
     delete). Refresh only when it's safe and useful: on the library screen,
     no modal open, no backups being started from here. */
  BV.state.on("library-dirty", function () {
    var onHome = !location.hash || location.hash === "#" || location.hash === "#home";
    if (!onHome || BV.modalOpen() || BV.jobs.activeCount()) return;
    refresh();
  });

  /* per-row progress bars: painted from the global jobs poller's events
     whenever the library is on screen (jobs.js owns the polling + the strip) */
  var _hadActiveJobs = false;   /* for the run-just-ended edge below */
  BV.state.on("jobs", function (ev) {
    if (!_libWrap || !document.body.contains(_libWrap)) return;
    var ids = Object.keys(ev.jobs || {});
    var anyActive = false;
    ids.forEach(function (jobId) {
      var p = ev.jobs[jobId];
      var terminal = BV.jobs.isTerminal(p);
      if (!terminal) anyActive = true;
      /* paint running jobs every tick; terminal ones once, when they land */
      if (terminal && (ev.newlyDone || []).indexOf(jobId) < 0) return;
      var rid = robotIdForJob(p);
      if (rid) renderRowProgress(rid, p);
    });
    setCancelAllVisible(anyActive);
    /* the run just ENDED: re-list once so rows settle on the truth (fresh
       "last" dates, partial pills only where a pull really died). The library
       watcher would catch it a few seconds later; this closes the gap. */
    var ended = _hadActiveJobs && !anyActive;
    _hadActiveJobs = anyActive;
    if (ended && !BV.modalOpen()) refresh();
  });

  /* which library row a job belongs to: tracked meta first (we started it),
     else match the job's host against robot IPs (jobs re-discovered after a
     reload were started by a previous page) */
  function robotIdForJob(p) {
    var meta = BV.jobs.meta(p.id);
    if (meta.robotId) return meta.robotId;
    var host = p.host || "";
    if (!host) return null;
    var r = _robots.find(function (x) { return (x.ips || []).indexOf(host) >= 0; });
    return r ? r.id : null;
  }

  function refresh() {
    if (_libWrap && document.body.contains(_libWrap)) loadLibrary();
  }

  /* repaint the tree from the cached listing — no lib_list, no rescan. Filter
     and sort both live here: they change how the same data is shown, and a
     refetch mid-backup would rescan a tree that is being written to. */
  function rerenderFromCache() {
    var body = _libWrap && _libWrap.querySelector(".home-lib-body");
    if (!body || !_lastData) { refresh(); return; }
    var scroller = document.getElementById("view");
    var keep = scroller ? scroller.scrollTop : 0;
    renderTree(body, _lastData);
    if (scroller) scroller.scrollTop = keep;
    reattachProgress();
  }

  function updateHiddenToggle(hiddenCount) {
    if (!_showHiddenBtn) return;
    _showHiddenBtn.classList.toggle("hidden", hiddenCount === 0);
    _showHiddenBtn.textContent = (_showHidden ? "hide hidden" : "show hidden") +
      (hiddenCount ? " (" + hiddenCount + ")" : "");
  }

  /* the pinned favorites strip above the plant tree: every starred entry as a
     FULL library row (checkbox included — select here, hit backup, done) with
     plant/line context, and a favorited robot brings its linked cameras along
     nested under it, so pinning one pins the whole station. Fold state
     survives refreshes like the tree's. */
  var _favOpen = true;

  function favSection(shownList) {
    var q = _filter;
    var favs = shownList.filter(function (r) {
      if (!r.favorite) return false;
      if (!q) return true;
      return [r.robot || "", r.model || "", r.notes || "", (r.ips || []).join(" "),
              r.plant || "", r.line || ""].join(" ").toLowerCase().indexOf(q) >= 0;
    });
    if (!favs.length) return null;
    favs = favs.slice().sort(robotComparator());
    /* unstarred cameras ride along under their favorited robot; a camera
       starred in its own right renders standalone via `favs` (never twice) */
    var favIds = {};
    favs.forEach(function (r) { favIds[r.id] = 1; });
    var camsByRobot = {};
    shownList.forEach(function (c) {
      if ((c.device_type || "").indexOf("camera") === 0 && !c.favorite &&
          c.linked_robot_id && favIds[c.linked_robot_id]) {
        (camsByRobot[c.linked_robot_id] = camsByRobot[c.linked_robot_id] || []).push(c);
      }
    });
    var node = BV.el("div", { class: "lib-plant lib-favs" });
    var head = BV.el("div", { class: "lib-plant-h" });
    head.appendChild(BV.el("span", null, "★ favorites"));
    var count = BV.el("span", { class: "lib-count" });
    head.appendChild(count);
    var bodyEl = BV.el("div", { class: "lib-plant-body" });
    var total = 0;
    favs.forEach(function (r) {
      bodyEl.appendChild(robotRow(r, false, { where: true }));
      total++;
      (camsByRobot[r.id] || []).sort(robotComparator()).forEach(function (c) {
        bodyEl.appendChild(robotRow(c, true, { where: true }));
        total++;
      });
    });
    count.textContent = String(total);
    node.appendChild(head);
    node.appendChild(bodyEl);
    BV.collapsible(node, head, bodyEl, {
      open: !!q || _favOpen,            /* filter matches never hide in a fold */
      onToggle: function (o) { _favOpen = o; },
    });
    return node;
  }

  function renderTree(body, data) {
    var robots = (data && data.robots) || [];
    /* one snapshot of who is mid-backup for this whole paint (not per row) */
    _liveTargets = (BV.jobs && BV.jobs.activeTargets)
      ? BV.jobs.activeTargets() : { ids: {}, hosts: {} };
    /* linked-camera counts, also once per paint: robotRow runs for every row,
       and a per-row filter() over the whole list is n² at plant scale */
    _camCounts = {};
    robots.forEach(function (c) {
      if (c.linked_robot_id)
        _camCounts[c.linked_robot_id] = (_camCounts[c.linked_robot_id] || 0) + 1;
    });
    /* the folder skeleton: empty plant/line folders are real structure the
       user built in Explorer — show them, so "make the folder, see the plant"
       holds even before any robots/backups exist inside */
    var empties = (data && data.empty_folders) || {};
    var emptyPlants = empties.plants || [];
    var emptyLines = empties.lines || [];
    if (!robots.length && !emptyPlants.length && !emptyLines.length) {
      body.innerHTML = '<div class="empty-lib">no robots yet — copy backups into the library folder, ' +
        "or discover robots on the network.</div>";
      updateHiddenToggle(0);
      _visibleRobots = [];
      _cl.sync();
      return;
    }
    var hiddenCount = 0;
    var shownList = robots.filter(function (r) {
      if (r.hidden) { hiddenCount++; return _showHidden; }
      return true;
    });
    updateHiddenToggle(hiddenCount);
    if (!shownList.length && !emptyPlants.length && !emptyLines.length) {
      body.innerHTML = '<div class="empty-lib">all robots are hidden — use “show hidden” above.</div>';
      _visibleRobots = [];
      _cl.sync();
      return;
    }
    var res = _tree.render(body,
      { robots: shownList, emptyPlants: emptyPlants, emptyLines: emptyLines },
      { q: _filter, cmp: robotComparator() });
    /* favorites pin ABOVE the tree (the tree render just wiped `body`) */
    var favNode = favSection(shownList);
    if (favNode) body.insertBefore(favNode, body.firstChild);
    /* the tree returns the robots IN RENDER ORDER (sorted groups + sorted
       robots), not cache order — anything order-sensitive (shift+click
       ranges) must see the list exactly as the user does */
    _visibleRobots = res.visible;
    if (_filterBox) _filterBox.setCount(_filter ? res.shown : undefined, res.total);
    _cl.sync();
  }

  /* opts (favorites-strip copies): where = show plant/line in the meta (a
     pinned row is far from its folders). Strip rows are FULL rows — checkbox
     included, so backup-the-selection works straight from favorites (the
     checklist repaints every bound copy of a key). */
  function robotRow(r, nested, opts) {
    opts = opts || {};
    var row = BV.el("div", { class: "lib-robot" + (r.stale ? " stale" : "") +
      (r.hidden ? " hidden-robot" : "") + (nested ? " lib-robot-nested" : "") });
    if (nested) row.style.cssText =
      "margin-left:1.6rem;border-left:2px solid var(--sub-alt);padding-left:0.6rem";
    row.setAttribute("data-robot-id", r.id);

    var cb = BV.el("input", { type: "checkbox", class: "lf-check lib-check",
      title: "select (shift+click selects a range)" });
    _cl.bind(cb, r.id);
    row.appendChild(cb);

    /* the star lives by the checkbox (both are "act on this row" controls).
       Toggling is INSTANT: flip the cached entry and repaint from cache — a
       lib_list here re-walks the whole tree, seconds at plant scale — then
       persist in the background and revert if the save fails. */
    var favBtn = BV.el("button", { class: "lib-fav" + (r.favorite ? " on" : ""),
      title: r.favorite ? "unpin from favorites" : "pin to favorites (top of the library)" },
      r.favorite ? "★" : "☆");
    favBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var want = !r.favorite;
      r.favorite = want;
      rerenderFromCache();
      BV.api.call("lib_set_favorite", r.id, want).catch(function (err) {
        r.favorite = !want;
        rerenderFromCache();
        BV.toast(err.message);
      });
    });
    row.appendChild(favBtn);

    var main = BV.el("div", { class: "lib-robot-main" });
    var nameHtml = '<span class="lib-robot-name">' + BV.esc(r.robot || "(unnamed)") + "</span>";
    if (r.model) nameHtml += '<span class="lib-robot-model">' + BV.esc(r.model) + "</span>";
    if (r.device_type === "camera-mtx") nameHtml += ' <span class="pill acc">mtx cam</span>';
    else if (r.device_type === "camera-keyence") nameHtml += ' <span class="pill acc">cv-x cam</span>';
    else {
      /* which twin holds the cameras? (duplicate robot names exist across lines,
         and a link to the WRONG twin looks like "nothing happened") */
      var nCams = _camCounts[r.id] || 0;
      if (nCams) nameHtml += ' <span class="pill acc">' + nCams + " cam" + (nCams > 1 ? "s" : "") + "</span>";
    }
    main.appendChild(BV.el("div", null, nameHtml));
    var meta = [];
    if (opts.where) {
      var where = [r.plant, r.line].filter(Boolean).join(" / ");
      if (where) meta.push(BV.esc(where));
    }
    if (r.ips && r.ips.length) meta.push(BV.esc(r.ips[0]));
    if (r.last_backup) meta.push("last " + BV.esc(r.last_backup));
    if (r.backups && r.backups.length) meta.push(r.backups.length + " saved");
    if (r.stale) meta.push('<span class="pill warn">missing</span>');
    /* while a pull is RUNNING for this robot its newest snapshot is partial by
       design — the row's live progress bar tells that story; the warning pills
       below would just cry wolf about a backup that is still being written */
    var backingUp = BV.jobs && BV.jobs.isRobotActive && BV.jobs.isRobotActive(r, _liveTargets);
    /* newest snapshot is a partial (a pull that died mid-download): say so -
       "last <date>" above is already the last COMPLETE one */
    if (!backingUp && r.backups && r.backups.length && r.backups[0].partial) {
      meta.push('<span class="pill warn" title="the newest snapshot is a partial backup ' +
        '(the pull never finished) — opening latest uses the last complete one">partial</span>');
    }
    if (!backingUp && !r.latest_path && !(r.backups && r.backups.length) && !r.stale) {
      meta.push('<span class="pill ghost">no backup</span>');
    }
    main.appendChild(BV.el("div", { class: "lib-robot-meta" },
      meta.join(' <span class="sep">·</span> ')));
    appendNote(main, r);
    row.appendChild(main);

    var prog = BV.el("div", { class: "lib-robot-progress" });
    row.appendChild(prog);

    var acts = BV.el("div", { class: "lib-robot-acts" });
    var moreBtn = BV.el("button", { class: "btn lib-robot-more",
      title: "actions (or right-click the row)" }, "⋯");
    moreBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      BV.menu(moreBtn, rowMenuItems(r, main));
    });
    acts.appendChild(moreBtn);
    row.appendChild(acts);

    /* right-click anywhere on the row = the same menu, at the mouse */
    row.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      e.stopPropagation();
      BV.menu({ x: e.clientX, y: e.clientY }, rowMenuItems(r, main));
    });

    /* whole row opens the robot, except the checkbox/star + action buttons
       (the star stops its own propagation).

       A click is dispatched on the COMMON ANCESTOR of its mousedown and its
       mouseup — so highlighting text in the note editor and releasing over
       the row lands a click on the ROW itself, sailing past the editor's own
       stopPropagation, and opened the backup mid-highlight. Judge the press,
       not just the release: where the drag STARTED (recorded in the capture
       phase, so a child's stopPropagation can't hide it), whether it ended a
       text selection, and whether a note was being edited at all. */
    var pressedIn = null, pressedEditing = false;
    row.addEventListener("mousedown", function (e) {
      pressedIn = e.target;
      /* read before the browser's focus change (a mousedown default action,
         which runs after dispatch) blurs the editor away */
      pressedEditing = !!row.querySelector(".lib-note-edit");
    }, true);
    row.addEventListener("click", function (e) {
      var from = pressedIn;
      pressedIn = null;
      if (cb.contains(e.target) || acts.contains(e.target)) return;
      /* a press while the note editor was open only commits the edit */
      if (pressedEditing) { pressedEditing = false; return; }
      if (from && from.closest && from.closest(NO_OPEN_SEL)) return;
      var sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed && sel.anchorNode && row.contains(sel.anchorNode)) {
        return;                      /* text was being highlighted, not clicked */
      }
      openRobot(r);
    });
    return row;
  }

  /* press here and the row never opens, wherever the mouse comes up */
  var NO_OPEN_SEL = ".lib-note-edit, .lib-robot-note, .lib-robot-note-head, " +
    ".lib-check, .lib-fav, .lib-robot-acts";

  /* one menu for the ⋯ button AND right-click on the row. No delete here:
     files are law — hide covers the everyday case, and a true delete is done
     in Explorer ("open folder"); the library follows. */
  function rowMenuItems(r, main) {
    var items = [
      { label: "edit", onClick: function () { editRobotModal(r, false); } },
      { label: r.notes ? "edit note" : "add note",
        onClick: function () { editNoteInline(main, r); } },
      { label: r.hidden ? "unhide" : "hide", onClick: function () { setHidden(r, !r.hidden); } },
    ];
    if (r.history_root) {
      items.push({ label: "open folder", onClick: function () { openLocation(r.history_root); } });
    }
    return items;
  }

  /* a robot's note in the listing: first line in grey, the rest behind an
     expand caret (reusing BV.collapsible — the row grows in place). A
     single-line note shows plain. Double-click any of it to edit right here. */
  function appendNote(main, r) {
    var text = (r.notes || "").replace(/\r\n?/g, "\n");
    if (!text.trim()) return;
    var lines = text.split("\n");
    var first = (lines[0] || "").trim() || "(note)";
    var rest = lines.slice(1).join("\n").replace(/\s+$/, "");
    if (!rest.trim()) {
      var solo = BV.el("div", { class: "lib-robot-note-head solo",
        title: "double-click to edit the note" },
        '<span class="note-line">' + BV.esc(first) + "</span>");
      solo.addEventListener("click", function (e) { e.stopPropagation(); });
      solo.addEventListener("dblclick", function (e) {
        e.stopPropagation();
        editNoteInline(main, r);
      });
      main.appendChild(solo);
      return;
    }
    var node = BV.el("div", { class: "lib-robot-note",
      title: "click to expand · double-click to edit" });
    var head = BV.el("div", { class: "lib-robot-note-head" },
      '<span class="note-line">' + BV.esc(first) + "</span>");
    var body = BV.el("div", { class: "lib-robot-note-body" }, BV.esc(rest));
    node.appendChild(head);
    node.appendChild(body);
    BV.collapsible(node, head, body, { open: false });
    /* clicks anywhere on the note (toggling, selecting text in the open body,
       right-click expand-all) must never open the robot */
    node.addEventListener("click", function (e) { e.stopPropagation(); });
    node.addEventListener("contextmenu", function (e) { e.stopPropagation(); });
    node.addEventListener("dblclick", function (e) {
      e.stopPropagation();
      editNoteInline(main, r);
    });
    main.appendChild(node);
  }

  /* Tab inserts a real tab instead of leaving the field — notes are lists */
  function wireNotesKeys(ta) {
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Tab") {
        e.preventDefault();
        var s = ta.selectionStart, en = ta.selectionEnd;
        ta.value = ta.value.slice(0, s) + "\t" + ta.value.slice(en);
        ta.selectionStart = ta.selectionEnd = s + 1;
      }
    });
  }

  /* swap a robot's note DISPLAY back in on every rendered copy of its row
     (tree + favorites strip) — surgical on purpose: a note edit must never
     rebuild the whole tree, which is a visible stall at plant scale */
  function repaintNotes(r) {
    if (!_libWrap) return;
    _libWrap.querySelectorAll('.lib-robot[data-robot-id="' + r.id + '"]')
      .forEach(function (row) {
        var main = row.querySelector(".lib-robot-main");
        if (!main) return;
        main.querySelectorAll(".lib-robot-note, .lib-robot-note-head.solo, .lib-note-edit")
          .forEach(function (n) { n.remove(); });
        appendNote(main, r);
      });
  }

  /* inline note editor right on the library row (double-click the note, or
     ⋯ → add/edit note): real multi-line — Enter adds a line, Tab indents,
     Esc cancels, clicking away saves. No modal, no leaving the library. */
  function editNoteInline(main, r) {
    var existing = main.querySelector(".lib-note-edit");
    if (existing) { existing.focus(); return; }
    /* hide the display while editing; the close paths repaint it */
    main.querySelectorAll(".lib-robot-note, .lib-robot-note-head.solo")
      .forEach(function (n) { n.style.display = "none"; });
    var ta = BV.el("textarea", { class: "lib-note-edit", spellcheck: "false",
      placeholder: "notes — Enter for a new line, Tab indents · Esc cancels, click away saves" });
    ta.value = r.notes || "";
    wireNotesKeys(ta);
    main.appendChild(ta);
    /* sizing does STRING math, not layout: reading scrollHeight per keystroke
       forces a reflow of the whole 2000-row page — that was the typing lag.
       The metrics are read once per open; growth happens only when the line
       COUNT changes, and overlong content scrolls inside the box instead. */
    var cs = getComputedStyle(ta);
    var lh = parseFloat(cs.lineHeight) || 16;
    var pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) + 2;
    var lastLines = -1;
    function autosize() {
      var lines = 1 + (ta.value.match(/\n/g) || []).length;
      if (lines === lastLines) return;      /* typing within a line: zero layout work */
      lastLines = lines;
      ta.style.height = Math.min(Math.max(lines, 2) * lh + pad, 220) + "px";
    }
    ta.addEventListener("input", autosize);
    /* the editor lives inside a clickable row — keep every mouse event local */
    ["mousedown", "click", "dblclick"].forEach(function (ev) {
      ta.addEventListener(ev, function (e) { e.stopPropagation(); });
    });
    var cancelled = false;
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        cancelled = true;              /* set BEFORE the repaint detaches us */
        repaintNotes(r);
      }
    });
    ta.addEventListener("blur", function () {
      if (cancelled) return;
      var text = ta.value.replace(/\s+$/, "");
      if (text === (r.notes || "")) { repaintNotes(r); return; }
      var prev = r.notes;
      r.notes = text;
      repaintNotes(r);                 /* instant, and only THIS robot's rows */
      BV.api.call("lib_update", r.id, { notes: text }).catch(function (err) {
        r.notes = prev;
        repaintNotes(r);
        BV.toast(err.message);
      });
    });
    ta.focus({ preventScroll: true });
    autosize();
    ta.selectionStart = ta.selectionEnd = ta.value.length;   /* ready to append */
  }

  function openRobot(r) {
    /* a dead Latest mirror is fine - lib_open falls back to the newest dated
       snapshot; only a robot with NO backups at all is unopenable */
    if (!r.latest_path && !(r.backups && r.backups.length)) {
      BV.toast("no backup yet — take one"); return;
    }
    if (r.stale) { BV.toast("backup folder missing on disk"); return; }
    BV.api.call("lib_open", r.id, "latest").then(function (manifest) {
      BV.state.setManifest(manifest);
      BV.toast(manifest.robot_name
        ? manifest.robot_name + " · " + manifest.file_count + " files" : "opened");
      location.hash = "#overview";
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* ---- selection ---- */

  function selectedInLine(lineRobots) {
    return lineRobots.filter(function (r) { return _cl.has(r.id); });
  }

  /* the sticky toolbar follows the selection: counter + button enablement.
     (checkbox + line tri-state repaints are the shared checklist's job —
     this runs as its onChange) */
  function syncToolbar() {
    if (!_libWrap) return;
    var selRobots = _visibleRobots.filter(function (r) { return _cl.has(r.id); });
    var selN = selRobots.length;
    var count = _libWrap.querySelector(".lib-sel-count");
    if (count) count.textContent = selN ? selN + " selected" : "";
    ["backup", "hide", "scan"].forEach(function (k) {
      var b = _libWrap.querySelector(".lib-act-" + k);
      if (b) b.disabled = selN === 0;
    });
    /* manage backups stays enabled with NO selection - its backup-health side
       (last run / partial / stale) needs no robots picked */
    var hd = _libWrap.querySelector(".lib-act-hide");
    if (hd) {
      /* the button says what it will DO: all-hidden selection -> unhide */
      var unhide = selN > 0 && selRobots.every(function (r) { return r.hidden; });
      hd.textContent = unhide ? "unhide" : "hide";
      hd.title = unhide ? "unhide the selected robots"
                        : "hide the selected robots from view";
    }
  }

  /* the manage-backups modal (manage_ui.js) drives the selected-robot flows
     without owning selection state or the flows themselves */
  BV.libActions = {
    selected: function () {
      return _visibleRobots.filter(function (r) { return _cl.has(r.id); });
    },
    fixNames: function () { fixNamesInLine(_visibleRobots); },
    merge: function () { mergeSelectedInLine(_visibleRobots); },
    moveTo: function () { moveSelectedFlow(_visibleRobots); },
    /* auto-link cameras to the robot they inspect (by name); no selection
       needed. Returns the promise so the caller can gate its button. */
    autoLink: function () {
      return BV.api.call("lib_auto_link").then(function (r) {
        var n = (r.linked || []).length, un = (r.unmatched || []).length,
            amb = (r.ambiguous || []).length;
        BV.toast("linked " + n + " camera" + (n === 1 ? "" : "s") +
          (amb ? " · " + amb + " ambiguous — same robot name in several lines; " +
                 "pick the right one in the camera's edit" : "") +
          (un ? " · " + un + " need manual linking (edit the camera)" : ""), 8000);
        refresh();
      });
    },
  };

  /* ---- per-line actions ---- */

  /* hide the selection — or UNHIDE it when every selected robot is hidden
     (with "show hidden" on, select the hidden ones and the button flips, so
     bulk unhide is one click instead of one row at a time) */
  function hideSelectedInLine(lineRobots) {
    var sel = selectedInLine(lineRobots);
    if (!sel.length) { BV.toast("select robots first"); return; }
    var unhide = sel.every(function (r) { return r.hidden; });
    Promise.all(sel.map(function (r) {
      return BV.api.call("lib_set_hidden", r.id, !unhide).then(function () { _cl.set(r.id, false); });
    })).then(function () {
      BV.toast(unhide ? "unhid " + sel.length
                      : "hid " + sel.length + " — files kept on disk");
      refresh();
    }).catch(function (e) { BV.toast(e.message); });
  }

  function setHidden(r, hidden) {
    BV.api.call("lib_set_hidden", r.id, hidden).then(function () {
      BV.toast(hidden ? "hidden" : "unhidden");
      refresh();
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* ---- rename / merge / tidy ---- */

  /* open a backup folder in the OS file manager (guarded to the library root) */
  function openLocation(path) {
    if (!path) { BV.toast("no folder on disk"); return; }
    BV.api.call("open_path", path).catch(function (e) { BV.toast(e.message); });
  }
  BV.openLocation = openLocation;   /* reused by the overview "open location" button */

  /* per-robot failure reasons for a batch toast: "NAME: why". The toast wraps
     at 64ch (pre-wrap), so a handful of reasons reads fine; longer batches
     clamp to the first few plus a count. */
  function failureLines(fails) {
    var lines = (fails || []).map(function (f) {
      return (f.robot || f.id || "?") + ": " + (f.error || "failed");
    });
    if (lines.length > 4) lines = lines.slice(0, 4).concat("+ " + (lines.length - 4) + " more");
    return lines;
  }

  /* is there ANOTHER saved robot already at this name+line? (the merge target) */
  function collidesWithExisting(excludeId, name, line) {
    var nm = (name || "").toUpperCase(), ln = (line || "").toUpperCase();
    for (var i = 0; i < _robots.length; i++) {
      var e = _robots[i];
      if (e.id === excludeId) continue;
      if ((e.robot || "").toUpperCase() === nm && (e.line || "").toUpperCase() === ln) return e;
    }
    return null;
  }

  /* "fix names from backups": auto-rename the clean ones, then prompt to merge any
     duplicates. Acts on the line's selected robots. */
  function fixNamesInLine(lineRobots) {
    var sel = selectedInLine(lineRobots);
    if (!sel.length) { BV.toast("select robots first"); return; }
    var ids = sel.map(function (r) { return r.id; });

    function clearSel() { ids.forEach(function (id) { _cl.set(id, false); }); }
    var failLines = [];   /* per-robot reasons collected across the steps */
    function done(nRenamed, nMerged) {
      var parts = [];
      if (nRenamed) parts.push("renamed " + nRenamed);
      if (nMerged) parts.push("merged " + nMerged);
      var msg = parts.length ? parts.join(" · ") : "no changes";
      if (failLines.length) msg += "\n" + failLines.join("\n");
      BV.toast(msg, failLines.length ? 8000 : undefined);
      clearSel();
      refresh();
    }

    BV.api.call("lib_resolve_names", ids).then(function (res) {
      var items = (res && res.items) || [];
      var renames = items.filter(function (it) { return it.action === "rename"; });
      var merges = items.filter(function (it) { return it.action === "merge"; });
      if (!renames.length && !merges.length) { BV.toast("names already match the backups"); return; }

      /* renames are folder moves — never apply them without showing the list */
      function applyRenames(chosen) {
        var step = chosen.length
          ? BV.api.call("lib_apply_renames", chosen.map(function (it) {
              return { id: it.id, plant: it.plant, line: it.line, robot: it.proposed };
            }))
          : Promise.resolve({ renamed: [], merged: [], failed: [] });

        step.then(function (rr) {
          var nRenamed = (rr.renamed || []).length;
          failLines = failLines.concat(failureLines(rr.failed));   /* named reasons, not a bare count */
          if (!merges.length) { done(nRenamed, (rr.merged || []).length); return; }
          confirmMergeBatch(merges, function (chosen) {
            if (!chosen.length) { done(nRenamed, 0); return; }
            var byTarget = {};
            chosen.forEach(function (it) {
              (byTarget[it.merge_into] = byTarget[it.merge_into] || []).push(it.id);
            });
            Promise.all(Object.keys(byTarget).map(function (primId) {
              return BV.api.call("lib_merge", primId, byTarget[primId]);
            })).then(function (results) {
              var nMerged = results.reduce(function (a, r) { return a + ((r.merged || []).length); }, 0);
              results.forEach(function (r) {
                (r.blocked || []).forEach(function (b) {
                  failLines.push(((b.secondary || {}).robot || "?") + ": not merged — " + (b.reason || ""));
                });
              });
              done(nRenamed, nMerged);
            }).catch(function (e) { BV.toast(e.message); refresh(); });
          }, function () { done(nRenamed, 0); });   /* user skipped the merges */
        }).catch(function (e) { BV.toast(e.message); refresh(); });
      }

      if (renames.length) {
        confirmRenameBatch(renames, applyRenames, function () { applyRenames([]); });
      } else {
        applyRenames([]);
      }
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* an "all" master box for modal checklists (rename / merge previews) —
     the same honest tri-state as the library's line select-alls */
  function listSelAll(cl, rows) {
    var lab = BV.el("label", { class: "list-selall" });
    lab.appendChild(cl.group(BV.el("input", { type: "checkbox", class: "lf-check" }),
      function () { return rows.map(function (r) { return r.key; }); }));
    lab.appendChild(BV.el("span", { class: "dim" }, "all"));
    cl.sync();                     /* reflect the pre-checked rows onto the box */
    return lab;
  }

  /* preview for the fix-names clean renames: current → proposed with per-row
     opt-outs. Folders only move after this confirm. */
  function confirmRenameBatch(renames, onConfirm, onSkip) {
    var body = BV.el("div", { class: "lib-form" });
    body.appendChild(BV.el("div", { class: "scan-info dim" },
      "These robots' backups report a different name. Rename their folders to match?"));
    var cl = BV.checklist();
    var list = BV.el("ul", { class: "merge-list" });
    var rows = [];
    renames.forEach(function (it, i) {
      var key = String(i);
      var li = BV.el("li");
      var lab = BV.el("label", { class: "rename-row" });
      cl.set(key, true);
      lab.appendChild(cl.bind(BV.el("input", { type: "checkbox", class: "lf-check" }), key));
      lab.appendChild(BV.el("span", null,
        " " + BV.esc(it.current || "(unnamed)") + " → " + BV.esc(it.proposed)));
      li.appendChild(lab);
      rows.push({ key: key, it: it });
      list.appendChild(li);
    });
    body.appendChild(listSelAll(cl, rows));
    body.appendChild(list);
    var acts = BV.el("div", { class: "lf-actions" });
    var skip = BV.el("button", { class: "btn" }, "skip renames");
    var go = BV.el("button", { class: "btn primary" }, "rename");
    acts.appendChild(skip); acts.appendChild(go); body.appendChild(acts);
    var m = BV.modal("fix names", body);
    skip.addEventListener("click", function () { m.close(); if (onSkip) onSkip(); });
    go.addEventListener("click", function () {
      m.close();
      onConfirm(rows.filter(function (r) { return cl.has(r.key); }).map(function (r) { return r.it; }));
    });
  }

  /* explicit merge of exactly 2 selected robots (always previews, direction is
     the user's call — the richer history is only the suggested default) */
  function mergeSelectedInLine(lineRobots) {
    var sel = selectedInLine(lineRobots);
    if (sel.length !== 2) { BV.toast("select exactly 2 robots to merge"); return; }
    var a = sel[0], b = sel[1];
    if ((a.line || "").toUpperCase() !== (b.line || "").toUpperCase()) {
      BV.toast("can't merge across lines"); return;
    }
    /* default: keep the richer history's id as the primary (swappable in the confirm) */
    var primary = ((a.backups || []).length >= (b.backups || []).length) ? a : b;
    var secondary = (primary === a) ? b : a;
    confirmSingleMerge(secondary, primary, function (prim, sec) {
      BV.api.call("lib_merge", prim.id, [sec.id]).then(function (res) {
        if ((res.refused || []).length) { BV.toast("refused — can't merge across lines"); refresh(); return; }
        if ((res.blocked || []).length) {
          /* the fold was a no-op (e.g. only non-dated content) — nothing changed,
             and saying "merged" here was exactly the field bug */
          BV.toast("nothing merged — " + (res.blocked[0].reason || "the other robot had nothing to fold in"), 6000);
          refresh(); return;
        }
        _cl.set(a.id, false); _cl.set(b.id, false);
        var m = (res.merged || [])[0] || {};
        var skipped = (m.skipped || []).length, conflicts = (m.conflicts || []).length;
        var msg = "merged " + (sec.robot || "") + " into " + (prim.robot || "");
        if (skipped) msg += " · " + skipped + " duplicate" + (skipped === 1 ? "" : "s") + " skipped";
        if (conflicts) msg += " · " + conflicts + " conflict" + (conflicts === 1 ? "" : "s") + " kept";
        BV.toast(msg);
        refresh();
      }).catch(function (e) { BV.toast(e.message); });
    }, { allowSwap: true });
  }

  /* bulk "move to": relocate every selected robot — folders and backups move
     with them — under a new plant/line in one dialog, instead of an edit per
     robot. Names are kept; a same-named robot already at the destination merges
     by the standard duplicate rules (identical snapshots skip, conflicts kept),
     and a different robot whose name merely collides is refused, not merged. */
  function moveSelectedFlow(robots) {
    var sel = selectedInLine(robots);
    if (!sel.length) { BV.toast("select robots first"); return; }
    var body = BV.el("div", { class: "lib-form" });
    body.appendChild(BV.el("div", { class: "scan-info dim" },
      "move " + sel.length + " robot" + (sel.length === 1 ? "" : "s") +
      " — and all of their backups — to:"));
    var fPlant = inp(""), fLine = inp("");
    body.appendChild(comboField("plant", fPlant, knownPlants));
    body.appendChild(comboField("line", fLine, function () { return knownLines(fPlant.value); }));
    var acts = BV.el("div", { class: "lf-actions" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var go = BV.el("button", { class: "btn primary" }, "move " + sel.length);
    acts.appendChild(cancel);
    acts.appendChild(go);
    body.appendChild(acts);
    var m = BV.modal("move to plant / line", body, {
      beforeClose: BV.dirtyGuard(function () {
        return !!(fPlant.value.trim() || fLine.value.trim());
      }, "move destination"),
    });
    cancel.addEventListener("click", m.close);
    fPlant.focus();
    go.addEventListener("click", function () {
      var plant = fPlant.value.trim(), line = fLine.value.trim();
      if (!line) { BV.toast("a line name is required"); fLine.focus(); return; }
      go.disabled = true;
      var items = sel.map(function (r) {
        return { id: r.id, plant: plant, line: line, robot: r.robot };
      });
      BV.api.call("lib_apply_renames", items).then(function (res) {
        m.close(true);
        var moved = (res.renamed || []).length, merged = (res.merged || []).length;
        var fl = failureLines(res.failed);
        var parts = [];
        if (moved) parts.push("moved " + moved);
        if (merged) parts.push("merged " + merged + " into existing");
        var msg = parts.length ? parts.join(" · ") : "nothing to move";
        if (fl.length) msg += "\n" + fl.join("\n");   /* who failed and why, verbatim */
        BV.toast(msg, fl.length ? 8000 : undefined);
        sel.forEach(function (r) { _cl.set(r.id, false); });
        refresh();
      }).catch(function (e) { BV.toast(e.message); go.disabled = false; });
    });
  }

  /* the batched "Duplicate robots detected. Merge?" confirm (fix-names path).
     Every row is a checkbox (like the rename preview): merges backed by 2+
     identity signals (IP / F-number / hostname / master counts) come checked;
     weak single-signal suggestions come UNCHECKED and say why. onConfirm
     receives the checked subset. */
  function confirmMergeBatch(merges, onConfirm, onSkip) {
    var body = BV.el("div", { class: "lib-form" });
    body.appendChild(BV.el("div", { class: "del-warn" },
      "Duplicate robots detected. Merge them into the matching robot? " +
      "Backups are combined; identical snapshots are skipped."));
    var list = BV.el("ul", { class: "merge-list" });
    var byId = {};
    _robots.forEach(function (e) { byId[e.id] = e; });
    function savedCount(id) {
      var e = byId[id];
      var n = e && e.backups ? e.backups.length : 0;
      return " (" + n + " saved)";
    }
    var cl = BV.checklist();
    var rows = [];
    merges.forEach(function (it, i) {
      var into = it.target || (byId[it.merge_into] || {}).robot || it.proposed;
      var sure = it.confidence !== "maybe";
      var key = String(i);
      var li = BV.el("li", { class: "merge-row" + (sure ? "" : " weak") });
      var lab = BV.el("label", { class: "rename-row" });
      if (sure) cl.set(key, true);         /* weak evidence never merges by default */
      lab.appendChild(cl.bind(BV.el("input", { type: "checkbox", class: "lf-check" }), key));
      lab.appendChild(BV.el("span", null,
        " " + BV.esc(it.current || "(unnamed)") + savedCount(it.id) +
        " → " + BV.esc(into) + savedCount(it.merge_into)));
      li.appendChild(lab);
      var why = it.reason || (it.evidence || []).join(" + ");
      li.appendChild(BV.el("div", { class: "merge-why dim" },
        (sure ? "" : "⚠ weak — ") + BV.esc(why)));
      rows.push({ key: key, it: it });
      list.appendChild(li);
    });
    body.appendChild(listSelAll(cl, rows));
    body.appendChild(list);
    var acts = BV.el("div", { class: "lf-actions" });
    var skip = BV.el("button", { class: "btn" }, "skip merges");
    var go = BV.el("button", { class: "btn primary" }, "merge");
    acts.appendChild(skip); acts.appendChild(go); body.appendChild(acts);
    var m = BV.modal("merge duplicates", body);
    skip.addEventListener("click", function () { m.close(); if (onSkip) onSkip(); });
    go.addEventListener("click", function () {
      m.close();
      onConfirm(rows.filter(function (r) { return cl.has(r.key); }).map(function (r) { return r.it; }));
    });
  }

  /* single "are you sure?" merge confirm (explicit merge + edit-modal collision).
     onConfirm receives the FINAL (primary, secondary) — with opts.allowSwap the
     user can flip which robot survives; the edit-modal collision direction is
     fixed by the rename semantics, so it passes no opts. */
  function confirmSingleMerge(secondary, primary, onConfirm, opts) {
    var prim = primary, sec = secondary;
    var body = BV.el("div", { class: "lib-form" });
    var msg = BV.el("div", { class: "del-warn" });
    function paint() {
      msg.innerHTML =
        "Merge <b>" + BV.esc(sec.robot || "(unnamed)") + "</b> into <b>" +
        BV.esc(prim.robot || "(unnamed)") + "</b>? Their backups are combined under " +
        BV.esc(prim.robot || "this robot") + "; identical snapshots are skipped and " +
        BV.esc(sec.robot || "the other") + " is removed.";
    }
    paint();
    body.appendChild(msg);
    var acts = BV.el("div", { class: "lf-actions" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    acts.appendChild(cancel);
    if (opts && opts.allowSwap) {
      var swap = BV.el("button", { class: "btn", title: "swap which robot survives" }, "⇄ swap");
      swap.addEventListener("click", function () { var t = prim; prim = sec; sec = t; paint(); });
      acts.appendChild(swap);
    }
    var go = BV.el("button", { class: "btn primary" }, "merge");
    acts.appendChild(go); body.appendChild(acts);
    var m = BV.modal("merge robots", body);
    cancel.addEventListener("click", m.close);
    go.addEventListener("click", function () { m.close(); onConfirm(prim, sec); });
  }

  /* ---- multi-backup ---- */

  function startLineBackup(lineRobots) {
    var sel = selectedInLine(lineRobots);
    if (!sel.length) { BV.toast("select robots first"); return; }
    var runnable = sel.filter(function (r) { return r.ips && r.ips[0]; });
    var noip = sel.length - runnable.length;
    if (!runnable.length) { BV.toast("no IP on selected robot(s)"); return; }

    var needsPw = runnable.some(function (r) { return r.ftp && r.ftp.user; });
    promptSharedPassword(needsPw, function (pw) {
      /* one run_id per click: the durable backup log groups these jobs as ONE
         run, so "last run" + retry-failed survive the post-backup refresh */
      var runId = "run-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      runnable.forEach(function (r) {
        var spec = {
          host: r.ips[0], robot: r.robot, line: r.line, plant: r.plant,
          device_type: r.device_type || "robot",
          robot_id: r.id, run_id: runId,
          user: (r.ftp && r.ftp.user) || "",
          passive: !r.ftp || r.ftp.passive !== false,
          passwd: (r.ftp && r.ftp.user) ? pw : "",
          note: "",
        };
        /* a multi-camera station pulls each IP into its own CAM<n> subfolder;
           credentials (mtxuser/Matrox, or anonymous for CV-X) fill server-side */
        if ((r.device_type || "").indexOf("camera") === 0 && r.ips.length > 1) {
          spec.cameras = r.ips.map(function (ip, i) { return { label: "CAM" + (i + 1), host: ip }; });
        }
        renderRowProgress(r.id, { status: "pending", total: 0, done: 0 });
        BV.api.call("start_backup", spec).then(function (res) {
          BV.jobs.track(res.job_id, { robotId: r.id });
          setCancelAllVisible(true);
        }).catch(function (e) {
          renderRowProgress(r.id, { status: "error", error: e.message });
        });
        _cl.set(r.id, false);
      });
      _cl.sync();
      if (noip) BV.toast(noip + " skipped · no IP");
    });
  }

  /* one shared password prompt for the whole batch (FANUC default is anonymous) */
  function promptSharedPassword(needed, cont) {
    if (!needed) { cont(""); return; }
    var body = BV.el("div", { class: "lib-form" });
    var pw = BV.el("input", { type: "password", class: "lf-input", spellcheck: "false" });
    var row = BV.el("div", { class: "lf-row" });
    row.appendChild(BV.el("label", null, "password"));
    row.appendChild(pw);
    body.appendChild(row);
    var acts = BV.el("div", { class: "lf-actions" });
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var ok = BV.el("button", { class: "btn primary" }, "connect");
    acts.appendChild(cancel);
    acts.appendChild(ok);
    body.appendChild(acts);
    var m = BV.modal("ftp password (shared)", body);
    pw.focus();
    cancel.addEventListener("click", m.close);
    function go() { var v = pw.value; m.close(); cont(v); }
    ok.addEventListener("click", go);
    body.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
  }
  BV.promptSharedPassword = promptSharedPassword;   /* manage-backups retry reuses it */

  /* every rendered copy of the robot's row — the favorites strip duplicates a
     pinned robot above its tree row, and progress must paint on both */
  function rowProgressSlots(robotId) {
    if (!_libWrap) return [];
    var rows = _libWrap.querySelectorAll('.lib-robot[data-robot-id="' + robotId + '"]');
    return Array.prototype.map.call(rows, function (row) {
      return row.querySelector(".lib-robot-progress");
    }).filter(Boolean);
  }

  function renderRowProgress(robotId, p) {
    var slots = rowProgressSlots(robotId);
    if (!slots.length) return;
    var html;
    if (BV.jobs.isTerminal(p)) {
      var cls, txt;
      if (p.status === "done") { cls = "ok"; txt = "✓ " + p.done + " files"; }
      else if (p.status === "cancelled") { cls = ""; txt = "cancelled"; }
      else { cls = "err"; txt = "✗ " + (p.error || "failed"); }
      html = '<div class="lib-robot-result ' + cls + '">' + BV.esc(txt) + "</div>";
    } else {
      var pct = p.total ? Math.round(100 * p.done / p.total) : 8;
      html = '<div class="membar"><div class="mb-label"><span>' + BV.esc(BV.jobs.statusText(p)) +
        "</span><span>" + (p.total ? p.done + " / " + p.total : "") + "</span></div>" +
        '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div></div>';
      if (p.current) html += '<div class="bf-current dim">' + BV.esc(p.current) + "</div>";
    }
    slots.forEach(function (slot) {
      slot.innerHTML = html;
      /* a long FTP error must not stretch the row sideways: CSS clamps it to
         one ellipsized line; click (or hover) for the whole thing */
      if (p.status === "error" && p.error) {
        var res = slot.querySelector(".lib-robot-result");
        res.title = p.error + "  (click for details)";
        res.addEventListener("click", function (e) {
          e.stopPropagation();
          BV.toast(p.error, 7000);
        });
      }
    });
  }

  function setCancelAllVisible(v) {
    var b = _libWrap && _libWrap.querySelector("#lib-cancel-all");
    if (b) b.classList.toggle("hidden", !v);
  }

  function cancelAllBackups() {
    BV.jobs.cancelAll();
  }

  /* after any (re)render, repaint backups that are still running (jobs.js owns
     the polling — this is just a one-shot repaint of its latest snapshots) */
  function reattachProgress() {
    var jobs = BV.jobs.latest();
    var any = false;
    Object.keys(jobs).forEach(function (jobId) {
      var p = jobs[jobId];
      if (BV.jobs.isTerminal(p)) return;
      any = true;
      var rid = robotIdForJob(p);
      if (rid) renderRowProgress(rid, p);
    });
    setCancelAllVisible(any);
  }

  /* ---- add / edit modal ---- */

  function inp(value, attrs) {
    var a = { type: "text", value: value || "", spellcheck: "false", class: "lf-input" };
    if (attrs) Object.keys(attrs).forEach(function (k) { a[k] = attrs[k]; });
    return BV.el("input", a);
  }
  function field(label, el) {
    var row = BV.el("div", { class: "lf-row" });
    row.appendChild(BV.el("label", null, BV.esc(label)));
    row.appendChild(el);
    return row;
  }

  /* distinct existing values for the plant/line pickers: case-insensitive
     dedupe presenting the stored casing (so 'Plant1'/'PLANT1' can't split),
     hidden robots included (their folders still exist) */
  function distinctCI(values) {
    var seen = {}, out = [];
    values.forEach(function (v) {
      v = (v || "").trim();
      if (!v) return;
      var k = v.toUpperCase();
      if (!seen[k]) { seen[k] = true; out.push(v); }
    });
    return out.sort(function (a, b) { return a.localeCompare(b); });
  }
  function knownPlants() {
    return distinctCI(_robots.map(function (r) { return r.plant; }));
  }
  function knownLines(plant) {
    var p = (plant || "").trim().toUpperCase();
    return distinctCI(_robots.filter(function (r) {
      return !p || (r.plant || "").toUpperCase() === p;
    }).map(function (r) { return r.line; }));
  }

  /* a text input + "▾" picker of existing values — no more retyping the same
     plant/line everywhere; free typing still creates new ones */
  function comboField(label, input, getSuggestions) {
    var row = BV.el("div", { class: "lf-row" });
    row.appendChild(BV.el("label", null, BV.esc(label)));
    var wrap = BV.el("div", { class: "lf-combo" });
    wrap.appendChild(input);
    var pick = BV.el("button", { class: "btn lf-combo-btn", type: "button",
      title: "pick an existing " + label }, "▾");
    pick.addEventListener("click", function (e) {
      e.preventDefault();
      var vals = getSuggestions() || [];
      if (!vals.length) { BV.toast("no existing " + label + "s yet — type one"); return; }
      BV.menu(pick, vals.map(function (v) {
        return { label: v, onClick: function () {
          input.value = v;
          input.dispatchEvent(new Event("input", { bubbles: true }));
        } };
      }));
    });
    wrap.appendChild(pick);
    row.appendChild(wrap);
    return row;
  }

  function editRobotModal(entry, isNew) {
    entry = entry || {};
    var form = BV.el("div", { class: "lib-form" });

    var fPlant = inp(entry.plant);
    var fLine = inp(entry.line);
    var fRobot = inp(entry.robot || entry.robot_name);
    var fModel = inp(entry.model);
    var fType = BV.el("select", { class: "lf-input" });
    [["robot", "robot (FANUC)"], ["camera-mtx", "matrox camera"],
     ["camera-keyence", "keyence camera (CV-X)"]].forEach(function (o) {
      var opt = BV.el("option", { value: o[0] }, o[1]);
      if ((entry.device_type || "robot") === o[0]) opt.selected = true;
      fType.appendChild(opt);
    });
    /* a camera can be linked to the robot it inspects (shows in the robot's
       Cameras tab). The picker is the library tree in a modal (the same
       composable as compare-from-library): plant/line folders, count badges
       and a filter — duplicate robot names exist across lines (test-cell
       copies!), and the folders say which twin is which. */
    var linkedId = entry.linked_robot_id || "";
    var linkBtn = BV.el("button", { class: "btn", type: "button",
      title: "pick the robot this camera inspects" });
    function linkedLabel() {
      if (!linkedId) return "(none)";
      var lr = _robots.find(function (x) { return x.id === linkedId; });
      if (!lr) return "(missing robot)";
      var where = [lr.plant, lr.line].filter(Boolean).join("/");
      return lr.robot + (where ? " · " + where : "");
    }
    function syncLinkBtn() { linkBtn.textContent = linkedLabel(); }
    syncLinkBtn();
    linkBtn.addEventListener("click", function () { pickLinkedRobot(); });
    var fIps = inp((entry.ips || []).join(", "));
    var fPath = inp(entry.latest_path, entry.latest_path ? { readonly: "readonly" } : null);
    var fUser = inp((entry.ftp || {}).user);
    var fPassive = BV.el("input", { type: "checkbox", class: "lf-check" });
    if (!entry.ftp || entry.ftp.passive !== false) fPassive.checked = true;
    /* notes are a real multi-line box: Enter for lines, Tab for indent */
    var fNotes = BV.el("textarea", { class: "lf-input lf-notes", rows: "4",
      spellcheck: "false" });
    fNotes.value = entry.notes || "";
    wireNotesKeys(fNotes);
    var hasFolders = !isNew && !!entry.history_root;
    var fMove = BV.el("input", { type: "checkbox", class: "lf-check" });
    fMove.checked = true;   /* default ON: folders rename WITH the entry */

    form.appendChild(comboField("plant", fPlant, knownPlants));
    form.appendChild(comboField("line", fLine, function () { return knownLines(fPlant.value); }));
    form.appendChild(field("device name", fRobot));
    form.appendChild(field("model", fModel));
    form.appendChild(field("device", fType));
    var linkedRow = field("linked robot", linkBtn);
    form.appendChild(linkedRow);
    function syncLinkedVis() {
      linkedRow.classList.toggle("hidden", fType.value.indexOf("camera") !== 0);
    }
    fType.addEventListener("change", syncLinkedVis);
    syncLinkedVis();
    form.appendChild(field("ip(s)", fIps));
    form.appendChild(field("folder", fPath));
    form.appendChild(field("ftp user", fUser));
    form.appendChild(field("passive ftp", fPassive));
    form.appendChild(field("notes", fNotes));
    if (hasFolders) form.appendChild(field("also move backup folders", fMove));

    var actions = BV.el("div", { class: "lf-actions" });
    var test = BV.el("button", { class: "btn" }, "test connection");
    var cancel = BV.el("button", { class: "btn" }, "cancel");
    var save = BV.el("button", { class: "btn primary" }, isNew ? "add" : "save");
    actions.appendChild(test);
    actions.appendChild(cancel);
    actions.appendChild(save);
    form.appendChild(actions);

    /* pre-flight probe straight from the form (read-only, no backup): a wedged
       controller or an off/moved camera shows up here in seconds instead of as
       a timed-out backup. Uses the form's CURRENT values, saved or not. */
    test.addEventListener("click", function () {
      var ip = fIps.value.split(",")[0].trim();
      if (!ip) { BV.toast("enter an IP first"); return; }
      test.disabled = true;
      test.textContent = "testing…";
      BV.api.call("probe_controller",
                  { host: ip, device_type: fType.value, user: fUser.value.trim() })
        .then(function (r) {
          if (!r.reachable) {
            BV.toast("no answer from " + ip + (r.error ? " · " + r.error : ""), 6000);
          } else if (fType.value === "camera-mtx") {
            BV.toast("matrox camera reachable · da " + (r.has_da ? "✓" : "✗") +
                     " · images " + (r.has_images ? "✓" : "✗"), 6000);
          } else if (fType.value === "camera-keyence") {
            BV.toast("CV-X camera reachable · setting " + (r.has_setting ? "✓" : "✗"), 6000);
          } else {
            BV.toast("robot reachable · MD: " + (r.has_md ? "✓" : "✗"), 6000);
          }
        })
        .catch(function (e) { BV.toast("probe failed: " + e.message, 6000); })
        .finally(function () { test.disabled = false; test.textContent = "test connection"; });
    });

    /* unsaved-work guard: a stray click outside the form can't eat typed edits */
    function fieldsSnapshot() {
      return [fPlant.value, fLine.value, fRobot.value, fModel.value, fType.value,
              linkedId, fIps.value, fPath.value, fUser.value, fPassive.checked,
              fNotes.value].join("\n");
    }
    var initialSnapshot = fieldsSnapshot();
    var isCam = (entry.device_type || "robot").indexOf("camera") === 0;
    var modalOpts = {
      beforeClose: BV.dirtyGuard(function () { return fieldsSnapshot() !== initialSnapshot; },
                                 "device edits"),
    };
    var m;
    function openEditModal() {
      m = BV.modal((isNew ? "add " : "edit ") + (isCam ? "camera" : "robot"), form, modalOpts);
    }
    openEditModal();
    cancel.addEventListener("click", function () { m.close(); });
    fRobot.focus();

    /* the linked-robot picker replaces the edit modal (modals don't stack) and
       brings it back on ANY close — the form element survives detached, so
       typed edits and listeners carry through (discover's back-button move) */
    function pickLinkedRobot() {
      var robots = _robots.filter(function (r) { return (r.device_type || "robot") === "robot"; });
      if (!robots.length) { BV.toast("no robots in the library to link"); return; }
      m.close(true);
      var pmodal;
      var tree = BV.libTree({
        counts: true,
        /* land on the camera's own plant — the robot a camera inspects is
           almost always nearby; the rest starts folded with count badges */
        startOpen: function (key, kind) {
          if (kind !== "plant") return true;
          var pl = (fPlant.value.trim() || entry.plant || "").toUpperCase();
          return !pl || key.toUpperCase() === pl;
        },
        row: function (r) {
          var row = BV.el("div", { class: "opt-row cmp-pick-row" + (r.id === linkedId ? " sel" : "") },
            '<span class="name">' + BV.esc(r.robot || "(unnamed)") +
            (r.model ? '<span class="lib-robot-model">' + BV.esc(r.model) + "</span>" : "") +
            "</span>" +
            ((r.ips && r.ips[0])
              ? '<span class="lib-robot-meta">' + BV.esc(r.ips[0]) + "</span>" : ""));
          row.addEventListener("click", function () {
            linkedId = r.id;
            syncLinkBtn();
            pmodal.close(true);
          });
          return row;
        },
      });
      var pbody = BV.el("div", { class: "cmp-pick" });
      var sb = BV.searchBox({
        placeholder: "filter robots…",
        onChange: function (q) { paint(q); },
      });
      var search = BV.el("div", { class: "cmp-pick-search" });
      search.appendChild(sb.el);
      pbody.appendChild(search);
      if (linkedId) {
        var clearRow = BV.el("div", { class: "opt-row cmp-pick-row" },
          '<span class="name" style="color:var(--sub)">✕ clear link (none)</span>');
        clearRow.addEventListener("click", function () {
          linkedId = "";
          syncLinkBtn();
          pmodal.close(true);
        });
        pbody.appendChild(clearRow);
      }
      var treeBody = BV.el("div");
      pbody.appendChild(treeBody);
      function paint(q) {
        var res = tree.render(treeBody, { robots: robots }, { q: q });
        sb.setCount(q ? res.shown : undefined, res.total);
      }
      paint("");
      pmodal = BV.modal("link to robot", pbody, {
        /* Esc clears a live filter first (the search-box convention); the
           edit modal comes back on ANY close — pick or cancel alike */
        beforeClose: function () {
          if (sb.value()) { sb.input.value = ""; paint(""); return false; }
          return true;
        },
        onClose: openEditModal,
      });
      sb.focus();
    }

    save.addEventListener("click", function () {
      var robot = fRobot.value.trim();
      if (!robot) { BV.toast("a device name is required"); return; }
      save.disabled = true;   /* double-click can't double-submit; re-enabled on failure */
      var fields = {
        plant: fPlant.value.trim(), line: fLine.value.trim(),
        robot: robot, model: fModel.value.trim(),
        device_type: fType.value,
        linked_robot_id: fType.value.indexOf("camera") === 0 ? linkedId : "",
        ips: fIps.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean),
        /* notes keep their leading indent (a list may start with a tab) —
           only trailing blank space is shed */
        latest_path: fPath.value.trim(), notes: fNotes.value.replace(/\s+$/, ""),
        ftp: { user: fUser.value.trim(), passive: fPassive.checked },
      };
      if (isNew) {
        var draft = {
          f_number: entry.f_number || "", backup_type: entry.backup_type || "",
          // preserve dated history + robot folder discovered while scanning
          backups: entry.backups || [], history_root: entry.history_root || "",
        };
        Object.keys(fields).forEach(function (k) { draft[k] = fields[k]; });
        BV.api.call("lib_add", draft)
          .then(function () { m.close(true); BV.toast("added"); refresh(); })
          .catch(function (e) { BV.toast(e.message); save.disabled = false; });
        return;
      }

      var idChanged = fields.plant !== (entry.plant || "") ||
                      fields.line !== (entry.line || "") ||
                      fields.robot !== (entry.robot || "");
      var moveFolders = hasFolders && fMove.checked && idChanged;

      if (!moveFolders) {
        BV.api.call("lib_update", entry.id, fields)
          .then(function () { m.close(true); BV.toast("saved"); refresh(); })
          .catch(function (e) { BV.toast(e.message); save.disabled = false; });
        return;
      }

      /* identity changed AND "move folders" on: relocate the tree, then write the
         non-identity fields onto the entry. A name+line collision is a merge —
         confirm it first (merges always confirm). */
      function relocateThenSave() {
        BV.api.call("lib_relocate", entry.id, fields.plant, fields.line, fields.robot)
          .then(function (res) {
            /* a merge into ANOTHER entry folds this robot into that survivor:
               the backend carries the useful config over — don't overwrite the
               survivor's own fields with this (now removed) robot's modal
               values. (res.id === entry.id is the orphan-folder ADOPTION case:
               the entry itself survives, so the user's edits still apply.) */
            if (res && res.action === "merged" && res.id !== entry.id) {
              m.close(true); BV.toast("merged"); refresh(); return;
            }
            /* relocate retargeted latest_path itself; the modal's readonly copy
               is the stale PRE-move path — never send it back */
            var patch = {};
            Object.keys(fields).forEach(function (k) {
              if (k !== "latest_path") patch[k] = fields[k];
            });
            BV.api.call("lib_update", entry.id, patch)
              .then(function () {
                m.close(true);
                BV.toast("moved & saved");
                refresh();
              })
              .catch(function (e) { m.close(true); BV.toast(e.message); refresh(); });
          })
          .catch(function (e) { BV.toast(e.message); save.disabled = false; });
      }

      var collide = collidesWithExisting(entry.id, fields.robot, fields.line);
      if (collide) {
        /* hand off to the merge confirm EXPLICITLY (it replaces this modal in
           #modal-root) so the edit modal's listeners and guard don't linger */
        m.close(true);
        confirmSingleMerge({ robot: fields.robot }, collide, relocateThenSave);
      } else {
        relocateThenSave();
      }
    });
  }

  /* (the "from backup" / "bulk from folder" import flows were removed in the
     v0.98 files-are-law pivot: copy backups into the library folder with
     Explorer and the scan/watcher lists them — no separate import step.) */

  /* ---- shared scan progress (network discover) ---- */

  function scanStatusText(p) {
    return {
      pending: "starting…", scanning: "scanning…",
      done: "done", error: "failed", cancelled: "cancelled",
    }[p.status] || p.status;
  }

  function renderScanBar(el, p) {
    var running = p.status === "pending" || p.status === "scanning";
    var pct = p.total ? Math.round(100 * p.scanned / p.total) : (running ? 8 : 100);
    var html = '<div class="membar"><div class="mb-label"><span>' + BV.esc(scanStatusText(p)) +
      "</span><span>" + (p.total ? p.scanned + " / " + p.total : "") +
      (p.found ? " · " + p.found + " found" : "") + "</span></div>" +
      '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div></div>';
    if (running && p.current) html += '<div class="bf-current dim">' + BV.esc(p.current) + "</div>";
    el.innerHTML = html;
  }

  /* poll one scan job every 500ms; onTick each poll, onDone at a terminal state.
     returns a stop() that halts polling (the modal calls it + cancel_scan on close) */
  function pollScan(jobId, onTick, onDone) {
    var iv = setInterval(function () {
      BV.api.call("scan_progress", jobId).then(function (p) {
        onTick(p);
        if (BV.jobs.isTerminal(p)) {
          clearInterval(iv);
          onDone(p);
        }
      }).catch(function () {
        clearInterval(iv);
        onDone({ status: "error", error: "scan lost", results: [] });
      });
    }, 500);
    return function stop() { clearInterval(iv); };
  }

  /* ---- discover robots on the network ---- */

  function discoverFlow() {
    var body = BV.el("div", { class: "lib-form disc-body" });

    /* network picker: an adapter dropdown (default = connected ethernet) with an
       "advanced" toggle that reveals the raw subnet/port dials for power users. */
    var adapters = [], chosenCidr = "";
    var adapterBtn = BV.el("button", { class: "btn disc-adapter", type: "button" }, "detecting…");
    var advToggle = BV.el("button", { class: "disc-advanced", type: "button" }, "advanced ▸");
    var advBox = BV.el("div", { class: "disc-adv-box hidden" });
    /* a bare IP is enough — the backend assumes /24 unless a CIDR is given */
    var fSubnet = inp("", { placeholder: "192.168.1.0" });
    var fPort = inp("21", { placeholder: "21" });
    advBox.appendChild(field("subnet", fSubnet));
    advBox.appendChild(field("port", fPort));

    /* plant/line are NOT asked here — scanning comes first; the add step asks
       for them right next to its confirm button, where the question is obvious */
    var bar = BV.el("div", { class: "scan-bar" });
    var selRow = BV.el("div", { class: "scan-selall hidden" });
    var selAll = BV.el("input", { type: "checkbox", class: "lf-check" });
    selRow.appendChild(selAll);
    selRow.appendChild(BV.el("span", null, "select all"));
    var devFilter = "all";   /* all | robots | cameras */
    var filterBox = BV.el("span", { class: "scan-filter", style: "margin-left:auto" });
    selRow.appendChild(filterBox);
    var list = BV.el("div", { class: "scan-results" });
    var actions = BV.el("div", { class: "lf-actions" });
    var scanBtn = BV.el("button", { class: "btn" }, "scan");
    var addBtn = BV.el("button", { class: "btn primary hidden" }, "add");
    addBtn.disabled = true;
    actions.appendChild(scanBtn);
    actions.appendChild(addBtn);
    /* two columns when the screen allows — network dials left, results right.
       The network row and the scan/add actions never scroll out of reach; ONLY
       the results list scrolls (the old single column scrolled the whole modal
       body, sinking the buttons below the fold on small screens). */
    var cols = BV.el("div", { class: "disc-cols" });
    var left = BV.el("div", { class: "disc-left" });
    left.appendChild(field("network", adapterBtn));
    left.appendChild(advToggle);
    left.appendChild(advBox);
    left.appendChild(bar);
    var right = BV.el("div", { class: "disc-right" });
    right.appendChild(selRow);
    right.appendChild(list);
    cols.appendChild(left);
    cols.appendChild(right);
    body.appendChild(cols);
    body.appendChild(actions);

    var advOpen = false;
    function setAdvanced(open) {
      advOpen = open;
      advBox.classList.toggle("hidden", !open);
      advToggle.textContent = open ? "advanced ▾" : "advanced ▸";
    }
    advToggle.addEventListener("click", function () { setAdvanced(!advOpen); });

    function adapterLabel(a) {
      return a.name + " · " + a.kind + " · " + a.ip + "/" + ((a.cidr || "").split("/")[1] || "24");
    }
    function setChosen(cidr, label) {
      chosenCidr = cidr || "";
      adapterBtn.textContent = label || (cidr || "auto (local subnet)");
      if (chosenCidr) fSubnet.value = chosenCidr;   /* keep the advanced field in sync */
    }
    adapterBtn.addEventListener("click", function () {
      var items = [];
      adapters.forEach(function (a) {
        if (!a.cidr) return;
        items.push({ label: adapterLabel(a), onClick: function () { setChosen(a.cidr, adapterLabel(a)); } });
      });
      items.push({ label: "enter manually…", onClick: function () {
        setAdvanced(true); setChosen("", "manual"); fSubnet.focus();
      } });
      BV.menu(adapterBtn, items);
    });

    var found = [], jobId = null, stop = null, scanning = false;
    var picks = BV.checklist({ onChange: updateAddBtn });  /* keyed by host IP */
    var m;
    var modalOpts = {
      /* selected scan results are work too — don't lose them to a stray click;
         closing (second press) still cancels a running scan via onClose */
      beforeClose: BV.dirtyGuard(function () {
        return found.some(function (h) { return picks.has(h.host); });
      }, "discovery picks"),
      onClose: function () { if (stop) stop(); if (jobId) BV.api.call("cancel_scan", jobId).catch(function () {}); },
    };
    /* the scan modal can be re-shown after "back" from the add step: BV.modal
       only detaches `body`, so the results list and its listeners survive */
    function openMain() {
      m = BV.modal("discover on network", body, modalOpts);
      m.el.classList.add("modal-disc");   /* wide two-column layout (re-added per open) */
    }
    openMain();

    /* populate adapters; default to the connected ethernet, else the local /24 */
    BV.api.call("list_adapters").then(function (res) {
      adapters = (res && res.adapters) || [];
      var def = null, i;
      for (i = 0; i < adapters.length; i++) { if (adapters[i].default && adapters[i].cidr) { def = adapters[i]; break; } }
      if (!def) { for (i = 0; i < adapters.length; i++) { if (adapters[i].cidr) { def = adapters[i]; break; } } }
      if (def) { setChosen(def.cidr, adapterLabel(def)); return; }
      var fb = (res && res.fallback) || {};
      setChosen(fb.cidr || "", fb.cidr ? "auto · " + fb.cidr : "auto (local subnet)");
      if (!adapters.length) setAdvanced(true);   /* no NICs enumerated: show manual dials */
    }).catch(function () {
      BV.api.call("local_subnet").then(function (s) {
        if (s && s.cidr) setChosen(s.cidr, "auto · " + s.cidr);
      }).catch(function () {});
    });

    function matchFilter(h) {
      var dt = h.device_type || "robot";
      if (devFilter === "robots") return dt === "robot";
      if (devFilter === "cameras") return dt.indexOf("camera") === 0;
      return true;
    }
    function visible() { return found.filter(matchFilter); }

    function renderFilter() {
      filterBox.innerHTML = "";
      if (!found.length) return;
      var nRobots = found.filter(function (h) { return (h.device_type || "robot") === "robot"; }).length;
      var nCams = found.filter(function (h) { return (h.device_type || "").indexOf("camera") === 0; }).length;
      var seg = BV.segmented([
        { id: "all", label: "all " + found.length },
        { id: "robots", label: "robots " + nRobots },
        { id: "cameras", label: "cameras " + nCams },
      ], { value: devFilter, onChange: function (id) { devFilter = id; renderList(); } });
      filterBox.appendChild(seg.el);
    }

    function updateAddBtn() {
      var n = found.filter(function (h) { return picks.has(h.host); }).length;
      /* hidden (not just disabled) until something is selected — people kept
         clicking a dead "add" before scanning */
      addBtn.classList.toggle("hidden", n === 0);
      addBtn.disabled = n === 0;
      addBtn.textContent = n ? "add " + n : "add";
    }

    function renderList() {
      selRow.classList.toggle("hidden", found.length === 0);
      renderFilter();
      list.innerHTML = "";
      visible().forEach(function (h) {
        var row = BV.el("div", { class: "scan-row" });
        var cb = picks.bind(BV.el("input", { type: "checkbox", class: "lf-check" }), h.host);
        row.appendChild(cb);
        var label = '<span class="lib-robot-name">' + BV.esc(h.name || h.host) + "</span>";
        var meta = [BV.esc(h.host)];
        if (h.device_type === "camera-mtx") {
          meta.push(BV.pill("MTX CAM", "acc"));
          if (h.model) meta.push(BV.esc(h.model));
          /* found by EtherNet/IP identity but no reachable SMB share yet */
          if (h.backup_ready === false) meta.push(BV.pill("no share", "warn"));
        } else if (h.device_type === "camera-keyence") {
          meta.push(BV.pill("CV-X CAM", "acc"));
          if (h.model) meta.push(BV.esc(h.model));
        }
        if (h.has_md) meta.push(BV.pill("MD", "acc"));
        if (h.has_fr) meta.push(BV.pill("FR", "acc"));
        label += ' <span class="lib-robot-meta">' + meta.join(" · ") + "</span>";
        row.appendChild(BV.el("div", { class: "lib-robot-main" }, label));
        list.appendChild(row);
      });
      picks.sync();
    }

    /* select-all covers only the VISIBLE (device-filtered) rows */
    picks.group(selAll, function () {
      return visible().map(function (h) { return h.host; });
    });

    scanBtn.addEventListener("click", function () {
      if (scanning) { if (jobId) BV.api.call("cancel_scan", jobId).catch(function () {}); return; }
      var cidr = advOpen ? fSubnet.value.trim() : (chosenCidr || fSubnet.value.trim());
      var port = parseInt(fPort.value, 10) || 21;
      found = []; picks.clear(); renderList();
      scanning = true; scanBtn.textContent = "stop"; addBtn.disabled = true;
      BV.api.call("net_scan_start", { cidr: cidr, port: port }).then(function (res) {
        jobId = res.job_id;
        stop = pollScan(jobId, function (p) {
          renderScanBar(bar, p);
          if ((p.results || []).length !== found.length) { found = p.results || []; renderList(); }
        }, function (p) {
          renderScanBar(bar, p);
          found = p.results || []; renderList();
          scanning = false; scanBtn.textContent = "scan";
          if (p.status === "done" && !found.length) BV.toast("no robots or cameras found");
        });
      }).catch(function (e) { BV.toast(e.message); scanning = false; scanBtn.textContent = "scan"; });
    });

    /* step 2: NOW ask where they go — the plant/line question sits right next
       to its confirm button instead of above a 38vh results list */
    function addStepTwo(drafts) {
      var body2 = BV.el("div", { class: "lib-form" });
      body2.appendChild(BV.el("div", { class: "scan-info dim" },
        "add " + drafts.length + " robot" + (drafts.length === 1 ? "" : "s") +
        " — which plant &amp; line?"));
      var fPlant = inp(""), fLine = inp("");
      body2.appendChild(comboField("plant", fPlant, knownPlants));
      body2.appendChild(comboField("line", fLine, function () { return knownLines(fPlant.value); }));
      var acts2 = BV.el("div", { class: "lf-actions" });
      var back = BV.el("button", { class: "btn" }, "← back");
      var go = BV.el("button", { class: "btn primary" },
        "add " + drafts.length);
      acts2.appendChild(back);
      acts2.appendChild(go);
      body2.appendChild(acts2);
      var m2 = BV.modal("add to library", body2, {
        beforeClose: BV.dirtyGuard(function () {
          return !!(fPlant.value.trim() || fLine.value.trim());
        }, "plant/line"),
      });
      back.addEventListener("click", function () { m2.close(true); openMain(); });
      go.addEventListener("click", function () {
        var line = fLine.value.trim();
        /* same rule as the move flow: a robot never lands without a line */
        if (!line) { BV.toast("a line name is required"); fLine.focus(); return; }
        go.disabled = true;
        BV.api.call("lib_bulk_add", drafts, fPlant.value.trim(), line).then(function (r) {
          m2.close(true);
          var added = (r.added || []).length, skipped = (r.skipped || []).length;
          BV.toast("added " + added + (skipped ? " · skipped " + skipped + " already in library" : ""));
          refresh();
        }).catch(function (e) { BV.toast(e.message); go.disabled = false; });
      });
      fPlant.focus();
    }

    addBtn.addEventListener("click", function () {
      var drafts = found.filter(function (h) { return picks.has(h.host); }).map(function (h) {
        return { robot: h.name || h.host, model: h.model || "", f_number: h.f_number || "",
          device_type: h.device_type || "robot",
          ips: [h.host], ftp: { user: "", passive: true } };
      });
      if (!drafts.length) return;
      m.close(true);              /* explicit handoff — the picks carry forward */
      addStepTwo(drafts);
    });
  }

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "home", label: "home", render: render, hidden: true, always: true, shell: true });
})();
