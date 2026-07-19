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
  var _collapsed = {};          /* folder keys explicitly collapsed (default = expanded) */
  var _selected = {};           /* selected robot ids (object used as a Set) */
  var _robots = [];             /* last loaded library list, for client-side collision checks */
  var _lastAbsorbMsg = "";      /* absorption toast dedupe (same folders every rescan) */
  var _lastClickedId = null;    /* shift+click range anchor (the last checkbox clicked) */
  var _showHidden = false;      /* reveal hidden robots in the list */
  var _showHiddenBtn = null;    /* the header toggle (shown only when some are hidden) */
  var _warnedTruncated = false; /* the scan-cap warning toast fires once per session */
  var _visibleRobots = [];      /* the currently-rendered robots — the sticky toolbar's scope */
  var _sortMode = "";           /* name | ip | date; lazily read from settings (lib_sort) */

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
    refresh();
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

  /* group keys alphabetically with the '—' (no plant/line) bucket pinned last */
  function groupKeys(obj) {
    return Object.keys(obj).sort(function (a, b) {
      if (a === "—") return 1;
      if (b === "—") return -1;
      return a.localeCompare(b);
    });
  }

  /* ---- folder collapse state (survives in-place refreshes) ---- */
  function isExpanded(key) { return !_collapsed[key]; }
  function setExpanded(key, open) { if (open) delete _collapsed[key]; else _collapsed[key] = true; }
  function makeCollapsible(node, head, body, key) {
    node.appendChild(head);
    node.appendChild(body);
    BV.collapsible(node, head, body, {
      open: isExpanded(key),
      onToggle: function (open) { setExpanded(key, open); },
    });
    return node;
  }

  /* ---- screen ---- */

  function render(view, toolbar, params) {
    _libWrap = BV.el("div", { class: "home-library" });
    view.appendChild(_libWrap);
    loadLibrary();
  }

  function loadLibrary() {
    if (!_libWrap) return;
    var body = _libWrap.querySelector(".home-lib-body");
    if (!body) {                                 /* first paint: build the shell once */
      _libWrap.innerHTML = "";
      _libWrap.appendChild(buildLibraryHead());
      body = BV.el("div", { class: "home-lib-body" }, '<div class="dim">loading…</div>');
      _libWrap.appendChild(body);
    }

    BV.api.call("lib_list").then(function (data) {
      _robots = (data && data.robots) || [];
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

    var selActs = BV.el("div", { class: "home-lib-selacts" });
    selActs.appendChild(BV.el("span", { class: "lib-sel-count dim" }, ""));
    var bk = BV.el("button", { class: "btn lib-act-backup", title: "back up the selected robots" },
      "backup");
    bk.addEventListener("click", function () { startLineBackup(_visibleRobots); });
    var hd = BV.el("button", { class: "btn lib-act-hide", title: "hide the selected robots from view" },
      "hide");
    hd.addEventListener("click", function () { hideSelectedInLine(_visibleRobots); });
    var fx = BV.el("button", { class: "btn lib-act-fix",
      title: "fix the selected robots' names from their backup contents" }, "fix names");
    fx.addEventListener("click", function () { fixNamesInLine(_visibleRobots); });
    var mg = BV.el("button", { class: "btn lib-act-merge",
      title: "merge 2 selected duplicate robots into one" }, "merge");
    mg.addEventListener("click", function () { mergeSelectedInLine(_visibleRobots); });
    var mv = BV.el("button", { class: "btn lib-act-move",
      title: "move the selected robots (and their backups) to another plant/line" }, "move to…");
    mv.addEventListener("click", function () { moveSelectedFlow(_visibleRobots); });
    selActs.appendChild(bk);
    selActs.appendChild(hd);
    selActs.appendChild(fx);
    selActs.appendChild(mg);
    selActs.appendChild(mv);
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
    var rescanBtn = BV.el("button", { class: "btn lib-rescan",
      title: "re-read the library folder from disk (picks up copied-in backups)" }, "rescan");
    rescanBtn.addEventListener("click", function () {
      rescanBtn.disabled = true;
      BV.api.call("lib_rescan")
        .then(function () { BV.toast("library rescanned"); rescanBtn.disabled = false; refresh(); })
        .catch(function (e) { BV.toast(e.message); rescanBtn.disabled = false; });
    });
    var linkBtn = BV.el("button", { class: "btn lib-link-cams",
      title: "auto-link cameras to the robot they inspect (by name)" }, "link cameras");
    linkBtn.addEventListener("click", function () {
      linkBtn.disabled = true;
      BV.api.call("lib_auto_link").then(function (r) {
        var n = (r.linked || []).length, un = (r.unmatched || []).length,
            amb = (r.ambiguous || []).length;
        BV.toast("linked " + n + " camera" + (n === 1 ? "" : "s") +
          (amb ? " · " + amb + " ambiguous — same robot name in several lines; " +
                 "pick the right one in the camera's edit" : "") +
          (un ? " · " + un + " need manual linking (edit the camera)" : ""), 8000);
        linkBtn.disabled = false; refresh();
      }).catch(function (e) { BV.toast(e.message); linkBtn.disabled = false; });
    });
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
    headActs.appendChild(linkBtn);
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
  BV.state.on("jobs", function (ev) {
    if (!_libWrap || !document.body.contains(_libWrap)) return;
    var ids = Object.keys(ev.jobs || {});
    var anyActive = false;
    ids.forEach(function (jobId) {
      var p = ev.jobs[jobId];
      var terminal = p.status === "done" || p.status === "error" || p.status === "cancelled";
      if (!terminal) anyActive = true;
      /* paint running jobs every tick; terminal ones once, when they land */
      if (terminal && (ev.newlyDone || []).indexOf(jobId) < 0) return;
      var rid = robotIdForJob(p);
      if (rid) renderRowProgress(rid, p);
    });
    setCancelAllVisible(anyActive);
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

  function updateHiddenToggle(hiddenCount) {
    if (!_showHiddenBtn) return;
    _showHiddenBtn.classList.toggle("hidden", hiddenCount === 0);
    _showHiddenBtn.textContent = (_showHidden ? "hide hidden" : "show hidden") +
      (hiddenCount ? " (" + hiddenCount + ")" : "");
  }

  function renderTree(body, data) {
    var robots = (data && data.robots) || [];
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
      syncSelectionUI();
      return;
    }
    var plants = {}, hiddenCount = 0, anyVisible = false;
    emptyPlants.forEach(function (pl) {
      plants[pl || "—"] = plants[pl || "—"] || {};
      anyVisible = true;
    });
    emptyLines.forEach(function (g) {
      var pl = g.plant || "—", ln = g.line || "—";
      plants[pl] = plants[pl] || {};
      plants[pl][ln] = plants[pl][ln] || [];
      anyVisible = true;
    });
    robots.forEach(function (r) {
      if (r.hidden) { hiddenCount++; if (!_showHidden) return; }
      anyVisible = true;
      var pl = r.plant || "—", ln = r.line || "—";
      plants[pl] = plants[pl] || {};
      plants[pl][ln] = plants[pl][ln] || [];
      plants[pl][ln].push(r);
    });
    updateHiddenToggle(hiddenCount);
    body.innerHTML = "";
    /* _visibleRobots is rebuilt below IN RENDER ORDER (sorted groups + sorted
       robots), not in cache order — anything order-sensitive (shift+click
       ranges) must see the list exactly as the user does */
    _visibleRobots = [];
    if (!anyVisible) {
      body.innerHTML = '<div class="empty-lib">all robots are hidden — use “show hidden” above.</div>';
      syncSelectionUI();
      return;
    }
    var cmp = robotComparator();
    groupKeys(plants).forEach(function (pl) {
      var plantNode = BV.el("div", { class: "lib-plant" });
      var plantHead = BV.el("div", { class: "lib-plant-h" }, BV.esc(pl));
      var plantBody = BV.el("div", { class: "lib-plant-body" });
      var lines = plants[pl];
      var lineKeys = groupKeys(lines);
      if (!lineKeys.length) {
        plantBody.appendChild(BV.el("div", { class: "dim lib-empty-note" },
          "empty plant folder — add line folders inside"));
      }
      lineKeys.forEach(function (ln) {
        var lineRobots = lines[ln].sort(cmp);
        lineRobots.forEach(function (r) { _visibleRobots.push(r); });
        var lineNode = BV.el("div", { class: "lib-line" });
        var lineHead = buildLineHead(ln, lineRobots);
        var lineBody = BV.el("div", { class: "lib-line-body" });
        if (!lineRobots.length) {
          lineBody.appendChild(BV.el("div", { class: "dim lib-empty-note" },
            "empty line folder — no robot folders yet"));
        }
        /* nest linked cameras under the robot they inspect (same line); unlinked
           cameras and cross-line links stay at top level so nothing disappears */
        var lineRobotIds = {};
        lineRobots.forEach(function (r) {
          if ((r.device_type || "robot") === "robot") lineRobotIds[r.id] = 1;
        });
        lineRobots.forEach(function (r) {
          var isCam = (r.device_type || "").indexOf("camera") === 0;
          if (isCam && r.linked_robot_id && lineRobotIds[r.linked_robot_id]) return;
          lineBody.appendChild(robotRow(r));
          lineRobots.forEach(function (c) {
            if ((c.device_type || "").indexOf("camera") === 0 && c.linked_robot_id === r.id) {
              lineBody.appendChild(robotRow(c, true));
            }
          });
        });
        makeCollapsible(lineNode, lineHead, lineBody, pl + "|||" + ln);
        plantBody.appendChild(lineNode);
      });
      makeCollapsible(plantNode, plantHead, plantBody, pl);
      body.appendChild(plantNode);
    });
    syncSelectionUI();
  }

  /* line header = name + select-all only; the action buttons live ONCE in the
     sticky library header (with 50+ lines, per-line button rows ate the screen) */
  function buildLineHead(ln, lineRobots) {
    var head = BV.el("div", { class: "lib-line-h" });
    head.appendChild(BV.el("span", { class: "lib-line-name" }, BV.esc(ln)));

    var controls = BV.el("div", { class: "lib-line-controls" });
    controls.addEventListener("click", function (e) { e.stopPropagation(); });

    var sa = BV.el("input", { type: "checkbox", class: "lf-check lib-line-selall",
      title: "select all in line" });
    sa.addEventListener("change", function () {
      var ids = lineRobots.map(function (r) { return r.id; });
      var allOn = ids.length && ids.every(function (id) { return _selected[id]; });
      ids.forEach(function (id) { if (allOn) delete _selected[id]; else _selected[id] = true; });
      syncSelectionUI();
    });

    controls.appendChild(sa);
    head.appendChild(controls);
    return head;
  }

  function robotRow(r, nested) {
    var row = BV.el("div", { class: "lib-robot" + (r.stale ? " stale" : "") +
      (r.hidden ? " hidden-robot" : "") + (nested ? " lib-robot-nested" : "") });
    if (nested) row.style.cssText =
      "margin-left:1.6rem;border-left:2px solid var(--sub-alt);padding-left:0.6rem";
    row.setAttribute("data-robot-id", r.id);

    var cb = BV.el("input", { type: "checkbox", class: "lf-check lib-check",
      title: "select (shift+click selects a range)" });
    cb.checked = !!_selected[r.id];
    cb.addEventListener("click", function (e) {
      e.stopPropagation();
      var on = cb.checked;               /* the checkbox has already toggled */
      var ids = null, a = -1, b = -1;
      if (e.shiftKey && _lastClickedId && _lastClickedId !== r.id && _libWrap) {
        /* the range is the rows as the user SEES them: DOM order at click
           time, skipping anything folded inside a collapsed plant/line —
           never silently select robots that aren't on screen */
        ids = Array.prototype.filter.call(
          _libWrap.querySelectorAll(".lib-robot[data-robot-id]"),
          function (el) { return el.offsetParent !== null; }
        ).map(function (el) { return el.getAttribute("data-robot-id"); });
        a = ids.indexOf(_lastClickedId);
        b = ids.indexOf(r.id);
      }
      if (ids && a >= 0 && b >= 0 && a !== b) {
        /* file-manager convention: the whole visible range between the last
           click and this one takes THIS click's new state */
        var lo = Math.min(a, b), hi = Math.max(a, b);
        for (var i = lo; i <= hi; i++) {
          if (on) _selected[ids[i]] = true; else delete _selected[ids[i]];
        }
      } else if (on) {
        _selected[r.id] = true;
      } else {
        delete _selected[r.id];
      }
      _lastClickedId = r.id;
      syncSelectionUI();
    });
    row.appendChild(cb);

    var main = BV.el("div", { class: "lib-robot-main" });
    var nameHtml = '<span class="lib-robot-name">' + BV.esc(r.robot || "(unnamed)") + "</span>";
    if (r.model) nameHtml += '<span class="lib-robot-model">' + BV.esc(r.model) + "</span>";
    if (r.device_type === "camera-mtx") nameHtml += ' <span class="pill acc">mtx cam</span>';
    else if (r.device_type === "camera-keyence") nameHtml += ' <span class="pill acc">cv-x cam</span>';
    else {
      /* which twin holds the cameras? (duplicate robot names exist across lines,
         and a link to the WRONG twin looks like "nothing happened") */
      var nCams = _robots.filter(function (c) { return c.linked_robot_id === r.id; }).length;
      if (nCams) nameHtml += ' <span class="pill acc">' + nCams + " cam" + (nCams > 1 ? "s" : "") + "</span>";
    }
    main.appendChild(BV.el("div", null, nameHtml));
    var meta = [];
    if (r.ips && r.ips.length) meta.push(BV.esc(r.ips[0]));
    if (r.last_backup) meta.push("last " + BV.esc(r.last_backup));
    if (r.backups && r.backups.length) meta.push(r.backups.length + " saved");
    if (r.stale) meta.push('<span class="pill warn">missing</span>');
    if (!r.latest_path && !r.stale) meta.push('<span class="pill ghost">no backup</span>');
    main.appendChild(BV.el("div", { class: "lib-robot-meta" },
      meta.join(' <span class="sep">·</span> ')));
    appendNote(main, r);
    row.appendChild(main);

    var prog = BV.el("div", { class: "lib-robot-progress" });
    row.appendChild(prog);

    var acts = BV.el("div", { class: "lib-robot-acts" });
    var editBtn = BV.el("button", { class: "btn", title: "edit" }, "edit");
    editBtn.addEventListener("click", function (e) { e.stopPropagation(); editRobotModal(r, false); });
    var moreBtn = BV.el("button", { class: "btn lib-robot-more", title: "more actions" }, "⋯");
    moreBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      /* no delete here: files are law — hide covers the everyday case, and a
         true delete is done in Explorer ("open folder"); the library follows. */
      var items = [
        { label: r.hidden ? "unhide" : "hide", onClick: function () { setHidden(r, !r.hidden); } },
      ];
      if (r.history_root) {
        items.push({ label: "open folder", onClick: function () { openLocation(r.history_root); } });
      }
      BV.menu(moreBtn, items);
    });
    acts.appendChild(editBtn);
    acts.appendChild(moreBtn);
    row.appendChild(acts);

    /* whole row opens the robot, except the checkbox + the action buttons */
    row.addEventListener("click", function (e) {
      if (cb.contains(e.target) || acts.contains(e.target)) return;
      openRobot(r);
    });
    return row;
  }

  /* a robot's note in the listing: first line in grey, the rest behind an
     expand caret (reusing BV.collapsible). A single-line note shows plain. */
  function appendNote(main, r) {
    var text = (r.notes || "").replace(/\r\n?/g, "\n");
    if (!text.trim()) return;
    var lines = text.split("\n");
    var first = (lines[0] || "").trim() || "(note)";
    var rest = lines.slice(1).join("\n").replace(/\s+$/, "");
    if (!rest.trim()) {
      var solo = BV.el("div", { class: "lib-robot-note-head solo" },
        '<span class="note-line">' + BV.esc(first) + "</span>");
      solo.addEventListener("click", function (e) { e.stopPropagation(); });
      main.appendChild(solo);
      return;
    }
    var node = BV.el("div", { class: "lib-robot-note" });
    var head = BV.el("div", { class: "lib-robot-note-head" },
      '<span class="note-line">' + BV.esc(first) + "</span>");
    var body = BV.el("div", { class: "lib-robot-note-body" }, BV.esc(rest));
    node.appendChild(head);
    node.appendChild(body);
    BV.collapsible(node, head, body, { open: false });
    /* don't let toggling (or right-click expand-all) open the robot */
    head.addEventListener("click", function (e) { e.stopPropagation(); });
    head.addEventListener("contextmenu", function (e) { e.stopPropagation(); });
    main.appendChild(node);
  }

  function openRobot(r) {
    if (!r.latest_path) { BV.toast("no backup yet — take one"); return; }
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
    return lineRobots.filter(function (r) { return _selected[r.id]; });
  }

  /* reflect _selected onto every checkbox + each line's select-all tri-state,
     and drive the sticky toolbar: selection counter + button enablement */
  function syncSelectionUI() {
    if (!_libWrap) return;
    _libWrap.querySelectorAll(".lib-line").forEach(function (lineNode) {
      var rows = lineNode.querySelectorAll(".lib-robot[data-robot-id]");
      var total = rows.length, on = 0;
      rows.forEach(function (rowEl) {
        var id = rowEl.getAttribute("data-robot-id");
        var cb = rowEl.querySelector(".lib-check");
        var checked = !!_selected[id];
        if (cb) cb.checked = checked;
        if (checked) on++;
      });
      var sa = lineNode.querySelector(".lib-line-selall");
      if (sa) {
        sa.checked = total > 0 && on === total;
        sa.indeterminate = on > 0 && on < total;
      }
    });
    var selRobots = _visibleRobots.filter(function (r) { return _selected[r.id]; });
    var selN = selRobots.length;
    var count = _libWrap.querySelector(".lib-sel-count");
    if (count) count.textContent = selN ? selN + " selected" : "";
    ["backup", "hide", "fix", "move"].forEach(function (k) {
      var b = _libWrap.querySelector(".lib-act-" + k);
      if (b) b.disabled = selN === 0;
    });
    var hd = _libWrap.querySelector(".lib-act-hide");
    if (hd) {
      /* the button says what it will DO: all-hidden selection -> unhide */
      var unhide = selN > 0 && selRobots.every(function (r) { return r.hidden; });
      hd.textContent = unhide ? "unhide" : "hide";
      hd.title = unhide ? "unhide the selected robots"
                        : "hide the selected robots from view";
    }
    var mg = _libWrap.querySelector(".lib-act-merge");
    if (mg) mg.disabled = selN !== 2;                /* merge is strictly a pair */
  }

  /* ---- per-line actions ---- */

  /* hide the selection — or UNHIDE it when every selected robot is hidden
     (with "show hidden" on, select the hidden ones and the button flips, so
     bulk unhide is one click instead of one row at a time) */
  function hideSelectedInLine(lineRobots) {
    var sel = selectedInLine(lineRobots);
    if (!sel.length) { BV.toast("select robots first"); return; }
    var unhide = sel.every(function (r) { return r.hidden; });
    Promise.all(sel.map(function (r) {
      return BV.api.call("lib_set_hidden", r.id, !unhide).then(function () { delete _selected[r.id]; });
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

    function clearSel() { ids.forEach(function (id) { delete _selected[id]; }); }
    function done(nRenamed, nMerged) {
      var parts = [];
      if (nRenamed) parts.push("renamed " + nRenamed);
      if (nMerged) parts.push("merged " + nMerged);
      BV.toast(parts.length ? parts.join(" · ") : "no changes");
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
          if ((rr.failed || []).length) BV.toast((rr.failed.length) + " rename(s) failed");
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

  /* preview for the fix-names clean renames: current → proposed with per-row
     opt-outs. Folders only move after this confirm. */
  function confirmRenameBatch(renames, onConfirm, onSkip) {
    var body = BV.el("div", { class: "lib-form" });
    body.appendChild(BV.el("div", { class: "scan-info dim" },
      "These robots' backups report a different name. Rename their folders to match?"));
    var list = BV.el("ul", { class: "merge-list" });
    var rows = [];
    renames.forEach(function (it) {
      var li = BV.el("li");
      var lab = BV.el("label", { class: "rename-row" });
      var cb = BV.el("input", { type: "checkbox", class: "lf-check" });
      cb.checked = true;
      lab.appendChild(cb);
      lab.appendChild(BV.el("span", null,
        " " + BV.esc(it.current || "(unnamed)") + " → " + BV.esc(it.proposed)));
      li.appendChild(lab);
      rows.push({ cb: cb, it: it });
      list.appendChild(li);
    });
    body.appendChild(list);
    var acts = BV.el("div", { class: "lf-actions" });
    var skip = BV.el("button", { class: "btn" }, "skip renames");
    var go = BV.el("button", { class: "btn primary" }, "rename");
    acts.appendChild(skip); acts.appendChild(go); body.appendChild(acts);
    var m = BV.modal("fix names", body);
    skip.addEventListener("click", function () { m.close(); if (onSkip) onSkip(); });
    go.addEventListener("click", function () {
      m.close();
      onConfirm(rows.filter(function (r) { return r.cb.checked; }).map(function (r) { return r.it; }));
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
        delete _selected[a.id]; delete _selected[b.id];
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
        var failed = (res.failed || []).length;
        var parts = [];
        if (moved) parts.push("moved " + moved);
        if (merged) parts.push("merged " + merged + " into existing");
        if (failed) parts.push(failed + " failed");
        BV.toast(parts.length ? parts.join(" · ") : "nothing to move", failed ? 5000 : undefined);
        sel.forEach(function (r) { delete _selected[r.id]; });
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
    var rows = [];
    merges.forEach(function (it) {
      var into = it.target || (byId[it.merge_into] || {}).robot || it.proposed;
      var sure = it.confidence !== "maybe";
      var li = BV.el("li", { class: "merge-row" + (sure ? "" : " weak") });
      var lab = BV.el("label", { class: "rename-row" });
      var cb = BV.el("input", { type: "checkbox", class: "lf-check" });
      cb.checked = sure;                   /* weak evidence never merges by default */
      lab.appendChild(cb);
      lab.appendChild(BV.el("span", null,
        " " + BV.esc(it.current || "(unnamed)") + savedCount(it.id) +
        " → " + BV.esc(into) + savedCount(it.merge_into)));
      li.appendChild(lab);
      var why = it.reason || (it.evidence || []).join(" + ");
      li.appendChild(BV.el("div", { class: "merge-why dim" },
        (sure ? "" : "⚠ weak — ") + BV.esc(why)));
      rows.push({ cb: cb, it: it });
      list.appendChild(li);
    });
    body.appendChild(list);
    var acts = BV.el("div", { class: "lf-actions" });
    var skip = BV.el("button", { class: "btn" }, "skip merges");
    var go = BV.el("button", { class: "btn primary" }, "merge");
    acts.appendChild(skip); acts.appendChild(go); body.appendChild(acts);
    var m = BV.modal("merge duplicates", body);
    skip.addEventListener("click", function () { m.close(); if (onSkip) onSkip(); });
    go.addEventListener("click", function () {
      m.close();
      onConfirm(rows.filter(function (r) { return r.cb.checked; }).map(function (r) { return r.it; }));
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

  function statusText(p) {
    return {
      connecting: "connecting…", listing: "listing files…", downloading: "downloading…",
      done: "done", error: "failed", cancelled: "cancelled", pending: "starting…",
    }[p.status] || p.status;
  }

  function startLineBackup(lineRobots) {
    var sel = selectedInLine(lineRobots);
    if (!sel.length) { BV.toast("select robots first"); return; }
    var runnable = sel.filter(function (r) { return r.ips && r.ips[0]; });
    var noip = sel.length - runnable.length;
    if (!runnable.length) { BV.toast("no IP on selected robot(s)"); return; }

    var needsPw = runnable.some(function (r) { return r.ftp && r.ftp.user; });
    promptSharedPassword(needsPw, function (pw) {
      runnable.forEach(function (r) {
        var spec = {
          host: r.ips[0], robot: r.robot, line: r.line, plant: r.plant,
          device_type: r.device_type || "robot",
          user: (r.ftp && r.ftp.user) || "",
          passive: !r.ftp || r.ftp.passive !== false,
          passwd: (r.ftp && r.ftp.user) ? pw : "",
          note: "",
        };
        /* a multi-camera station pulls each IP into its own CAM<n> subfolder;
           credentials (MTXuser/MATROX, or anonymous for CV-X) fill server-side */
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
        delete _selected[r.id];
      });
      syncSelectionUI();
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

  function rowProgressSlot(robotId) {
    if (!_libWrap) return null;
    var row = _libWrap.querySelector('.lib-robot[data-robot-id="' + robotId + '"]');
    return row ? row.querySelector(".lib-robot-progress") : null;
  }

  function renderRowProgress(robotId, p) {
    var slot = rowProgressSlot(robotId);
    if (!slot) return;
    if (p.status === "done" || p.status === "error" || p.status === "cancelled") {
      var cls, txt;
      if (p.status === "done") { cls = "ok"; txt = "✓ " + p.done + " files"; }
      else if (p.status === "cancelled") { cls = ""; txt = "cancelled"; }
      else { cls = "err"; txt = "✗ " + (p.error || "failed"); }
      slot.innerHTML = '<div class="lib-robot-result ' + cls + '">' + BV.esc(txt) + "</div>";
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
      return;
    }
    var pct = p.total ? Math.round(100 * p.done / p.total) : 8;
    var html = '<div class="membar"><div class="mb-label"><span>' + BV.esc(statusText(p)) +
      "</span><span>" + (p.total ? p.done + " / " + p.total : "") + "</span></div>" +
      '<div class="mb-track"><div class="mb-fill" style="width:' + pct + '%"></div></div></div>';
    if (p.current) html += '<div class="bf-current dim">' + BV.esc(p.current) + "</div>";
    slot.innerHTML = html;
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
      if (p.status === "done" || p.status === "error" || p.status === "cancelled") return;
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
       Cameras tab); the picker lists robot entries, hidden for robot entries */
    var fLinked = BV.el("select", { class: "lf-input" });
    fLinked.appendChild(BV.el("option", { value: "" }, "(none)"));
    _robots.filter(function (r) { return (r.device_type || "robot") === "robot"; })
      .forEach(function (r) {
        /* duplicate robot names exist across lines/plants (test-cell copies!) -
           show the full plant/line so the RIGHT twin gets linked */
        var where = [r.plant, r.line].filter(Boolean).join("/");
        var o = BV.el("option", { value: r.id }, r.robot + (where ? " · " + where : ""));
        if (entry.linked_robot_id === r.id) o.selected = true;
        fLinked.appendChild(o);
      });
    var fIps = inp((entry.ips || []).join(", "));
    var fPath = inp(entry.latest_path, entry.latest_path ? { readonly: "readonly" } : null);
    var fUser = inp((entry.ftp || {}).user);
    var fPassive = BV.el("input", { type: "checkbox", class: "lf-check" });
    if (!entry.ftp || entry.ftp.passive !== false) fPassive.checked = true;
    var fNotes = inp(entry.notes);
    var hasFolders = !isNew && !!entry.history_root;
    var fMove = BV.el("input", { type: "checkbox", class: "lf-check" });
    fMove.checked = true;   /* default ON: folders rename WITH the entry */

    form.appendChild(comboField("plant", fPlant, knownPlants));
    form.appendChild(comboField("line", fLine, function () { return knownLines(fPlant.value); }));
    form.appendChild(field("robot", fRobot));
    form.appendChild(field("model", fModel));
    form.appendChild(field("device", fType));
    var linkedRow = field("linked robot", fLinked);
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
              fLinked.value, fIps.value, fPath.value, fUser.value, fPassive.checked,
              fNotes.value].join("\n");
    }
    var initialSnapshot = fieldsSnapshot();
    var m = BV.modal(isNew ? "add robot" : "edit robot", form, {
      beforeClose: BV.dirtyGuard(function () { return fieldsSnapshot() !== initialSnapshot; },
                                 "robot edits"),
    });
    cancel.addEventListener("click", m.close);
    fRobot.focus();

    save.addEventListener("click", function () {
      var robot = fRobot.value.trim();
      if (!robot) { BV.toast("robot name required"); return; }
      var fields = {
        plant: fPlant.value.trim(), line: fLine.value.trim(),
        robot: robot, model: fModel.value.trim(),
        device_type: fType.value,
        linked_robot_id: fType.value.indexOf("camera") === 0 ? fLinked.value : "",
        ips: fIps.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean),
        latest_path: fPath.value.trim(), notes: fNotes.value.trim(),
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
          .catch(function (e) { BV.toast(e.message); });
        return;
      }

      var idChanged = fields.plant !== (entry.plant || "") ||
                      fields.line !== (entry.line || "") ||
                      fields.robot !== (entry.robot || "");
      var moveFolders = hasFolders && fMove.checked && idChanged;

      if (!moveFolders) {
        BV.api.call("lib_update", entry.id, fields)
          .then(function () { m.close(true); BV.toast("saved"); refresh(); })
          .catch(function (e) { BV.toast(e.message); });
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
          .catch(function (e) { BV.toast(e.message); });
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
        if (p.status === "done" || p.status === "error" || p.status === "cancelled") {
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
    var body = BV.el("div", { class: "lib-form" });

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
    body.appendChild(field("network", adapterBtn));
    body.appendChild(advToggle);
    body.appendChild(advBox);
    body.appendChild(bar);
    body.appendChild(selRow);
    body.appendChild(list);
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

    var found = [], sel = {}, jobId = null, stop = null, scanning = false;
    var m;
    var modalOpts = {
      /* selected scan results are work too — don't lose them to a stray click;
         closing (second press) still cancels a running scan via onClose */
      beforeClose: BV.dirtyGuard(function () {
        return found.some(function (h) { return sel[h.host]; });
      }, "discovery picks"),
      onClose: function () { if (stop) stop(); if (jobId) BV.api.call("cancel_scan", jobId).catch(function () {}); },
    };
    /* the scan modal can be re-shown after "back" from the add step: BV.modal
       only detaches `body`, so the results list and its listeners survive */
    function openMain() { m = BV.modal("discover on network", body, modalOpts); }
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
      var n = found.filter(function (h) { return sel[h.host]; }).length;
      /* hidden (not just disabled) until something is selected — people kept
         clicking a dead "add" before scanning */
      addBtn.classList.toggle("hidden", n === 0);
      addBtn.disabled = n === 0;
      addBtn.textContent = n ? "add " + n : "add";
      /* select-all reflects only the VISIBLE (filtered) rows */
      var vis = visible();
      var on = vis.length && vis.every(function (h) { return sel[h.host]; });
      selAll.checked = !!on;
      selAll.indeterminate = !on && vis.some(function (h) { return sel[h.host]; });
    }

    function renderList() {
      selRow.classList.toggle("hidden", found.length === 0);
      renderFilter();
      list.innerHTML = "";
      visible().forEach(function (h) {
        var row = BV.el("div", { class: "scan-row" });
        var cb = BV.el("input", { type: "checkbox", class: "lf-check" });
        cb.checked = !!sel[h.host];
        cb.addEventListener("change", function () { if (cb.checked) sel[h.host] = true; else delete sel[h.host]; updateAddBtn(); });
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
      updateAddBtn();
    }

    selAll.addEventListener("change", function () {
      var on = selAll.checked;
      visible().forEach(function (h) { if (on) sel[h.host] = true; else delete sel[h.host]; });
      renderList();
    });

    scanBtn.addEventListener("click", function () {
      if (scanning) { if (jobId) BV.api.call("cancel_scan", jobId).catch(function () {}); return; }
      var cidr = advOpen ? fSubnet.value.trim() : (chosenCidr || fSubnet.value.trim());
      var port = parseInt(fPort.value, 10) || 21;
      found = []; sel = {}; renderList();
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
        go.disabled = true;
        BV.api.call("lib_bulk_add", drafts, fPlant.value.trim(), fLine.value.trim()).then(function (r) {
          m2.close(true);
          var added = (r.added || []).length, skipped = (r.skipped || []).length;
          BV.toast("added " + added + (skipped ? " · skipped " + skipped + " already in library" : ""));
          refresh();
        }).catch(function (e) { BV.toast(e.message); go.disabled = false; });
      });
      fPlant.focus();
    }

    addBtn.addEventListener("click", function () {
      var drafts = found.filter(function (h) { return sel[h.host]; }).map(function (h) {
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
