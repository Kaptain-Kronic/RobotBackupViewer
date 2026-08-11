/* tabs/compare.js - changes-only diff of two backups (#compare, hidden tab).
   Entry: the topbar "compare" button picks the second backup folder. Each
   category is a collapsible section with +added −removed ~changed counts.

   View state (mode / filter / which sections are open / scroll position /
   hidden entries) persists across navigation so popping in and out of the
   report keeps your place. It resets when the compared PAIR changes (swap /
   change backup), but a refresh of the same pair keeps everything. Filtering
   and hiding toggle existing rows in place - the page never rebuilds/scrolls. */
(function () {
  "use strict";

  /* module scope = survives route changes; per-pair bits reset in render() */
  var cmpState = {
    pairKey: null, mode: "all", filter: "",
    expanded: {}, scrollTop: 0, hidden: {}, showHidden: false,
  };

  /* compound-key separator — see BV.KEYSEP in util.js for the NUL footgun */
  var KEYSEP = BV.KEYSEP;
  function hideKey(catId, name) { return catId + KEYSEP + name; }

  function countsHtml(cat) {
    var c = cat.counts || {};
    if (cat.id === "mastering") {
      return cat.ok
        ? BV.pill("differs ✓ (expected)", "on")
        : BV.pill("⚠ " + c.alert + " identical", "err");
    }
    var bits = [];
    if (c.added) bits.push(BV.pill("+" + c.added, "on"));
    if (c.removed) bits.push(BV.pill("−" + c.removed, "err"));
    if (c.changed) bits.push(BV.pill("~" + c.changed, "acc"));
    return bits.join(" ") || BV.pill("no differences", "ghost");
  }

  /* always two columns: what robot A has, what robot B has - "non-existent"
     spelled out instead of git-style one-sided rows. hidable rows get a hide
     button (top-level category rows; not the mini-diff leaf rows). */
  function rowHtml(r, catId, hidable) {
    var deep = catId === "programs" || catId === "pc";
    var clickable = deep && r.kind === "changed" && r.diffable;
    var cells;
    if (deep && r.kind === "changed" && r.summary) {
      cells = '<span class="cmp-summary">' + BV.esc(r.summary) +
        (clickable ? ' <span class="dim">— click to view</span>' : "") + "</span>";
    } else {
      var aCell = r.kind === "added"
        ? '<span class="cmp-missing">non-existent</span>'
        : '<span class="cmp-a">' + BV.esc(r.a || "—") + "</span>";
      var bCell = r.kind === "removed"
        ? '<span class="cmp-missing">non-existent</span>'
        : '<span class="cmp-b">' + BV.esc(r.b || "—") + "</span>";
      cells = aCell + bCell;
    }
    var commentHtml = (deep && r.comment)
      ? '<span class="cmp-prog-comment">' + BV.esc(r.comment) + "</span>" : "";
    var hideBtn = hidable
      ? '<span class="cmp-hide" title="hide this difference (verified OK) — “show hidden” to bring it back">✕</span>' : "";
    return '<div class="cmp-row cmp-' + r.kind + (clickable ? " cmp-click" : "") +
      '" data-name="' + BV.esc(r.name) + '"' +
      (clickable ? ' title="show the differing lines right here"' : "") + ">" +
      '<span class="cmp-name">' + BV.esc(r.name) + "</span>" + cells + commentHtml + hideBtn + "</div>";
  }

  function headerRowHtml(data) {
    return '<div class="cmp-row cmp-cols-head"><span class="cmp-name"></span>' +
      '<span>' + BV.esc(data.a.robot_name || data.a.name) + "</span>" +
      '<span>' + BV.esc(data.b.robot_name || data.b.name) + "</span></div>";
  }

  function sideHtml(info, cls) {
    return '<div class="cmp-side ' + cls + '">' +
      '<span class="robot-name" style="font-size:1.15rem">' + BV.esc(info.robot_name || info.name) + "</span>" +
      '<span class="hero-sub">' +
      [info.f_number, info.backup_type, info.backup_date].filter(Boolean).map(BV.esc).join(" · ") +
      "</span>" +
      '<span class="dim" style="font-size:.72rem" title="' + BV.esc(info.path) + '">' + BV.esc(info.name) + "</span></div>";
  }

  function sum(c) { return c.added + c.removed + c.changed; }

  function render(view, toolbar) {
    view.innerHTML = "";
    toolbar.innerHTML = "";
    if (!BV.state.compare) {
      view.innerHTML = '<div class="empty-state"><div class="big">compare two backups</div>' +
        '<div class="hint">open your main backup first, then hit <kbd>compare</kbd> up top<br>' +
        "and pick the second backup folder — you get a changes-only report</div></div>";
      return;
    }

    /* a different pair = a fresh comparison (clear filter/expand/scroll/hidden);
       mode is a sticky preference and carries over. A refresh keeps the pair. */
    var pairKey = ((BV.state.manifest && BV.state.manifest.path) || "") + KEYSEP + BV.state.compare.path;
    if (cmpState.pairKey !== pairKey) {
      cmpState.pairKey = pairKey;
      cmpState.filter = "";
      cmpState.expanded = {};
      cmpState.scrollTop = 0;
      cmpState.hidden = {};
      cmpState.showHidden = false;
    }

    var sb = BV.searchBox({
      placeholder: "filter changes…",
      onChange: function (q) { cmpState.filter = q.toLowerCase(); applyFilter(); },
    });
    sb.input.value = cmpState.filter;
    toolbar.appendChild(sb.el);
    BV.currentSearch = sb;

    /* comment/value mode re-fetches server-side (get_compare(mode)) */
    var modeSeg = BV.segmented(
      [{ id: "all", label: "everything" },
       { id: "no_comments", label: "ignore comments" },
       { id: "no_values", label: "ignore values" }],
      {
        value: cmpState.mode,
        onChange: function (id) {
          if (cmpState.mode === id) return;
          cmpState.mode = id;
          body.innerHTML = '<div class="dim" style="padding:.5rem 0">recomparing…</div>';
          BV.api.call("get_compare", cmpState.mode).then(function (res) {
            data = res;
            draw();
          }).catch(function (e) { BV.toast(e.message); });
        },
      }
    );
    modeSeg.el.title = "what counts as a difference";
    toolbar.appendChild(modeSeg.el);

    /* refresh: re-read both backup folders (after you've taken a fresh backup),
       keeping your place + hidden entries. Mirrors the swap-twice trick. */
    var refreshBtn = BV.el("button", {
      class: "btn",
      title: "re-read both backup folders (after a fresh backup) — keeps your place & hidden entries",
    }, "↻ refresh");
    refreshBtn.addEventListener("click", function () {
      if (!data) return;
      refreshBtn.disabled = true;
      BV.api.call("open_backup", data.a.path).then(function (m) {
        BV.state.setManifest(m);
        return BV.api.call("open_compare", data.b.path);
      }).then(function (cm) {
        BV.state.compare = cm;
        return BV.api.call("get_compare", cmpState.mode);
      }).then(function (res) {
        data = res;
        refreshBtn.disabled = false;
        draw();
        BV.toast("refreshed");
      }).catch(function (e) { refreshBtn.disabled = false; BV.toast(e.message); });
    });
    toolbar.appendChild(refreshBtn);

    var swapBtn = BV.el("button", { class: "btn", title: "swap which backup is the baseline" }, "swap a↔b");
    toolbar.appendChild(swapBtn);
    var changeBtn = BV.el("button", { class: "btn" }, "change backup");
    changeBtn.addEventListener("click", function () { BV.compareFlow(changeBtn); });
    toolbar.appendChild(changeBtn);
    var clearBtn = BV.el("button", { class: "btn" }, "clear");
    clearBtn.addEventListener("click", function () {
      BV.api.call("close_compare").catch(function () {});
      BV.state.compare = null;
      location.hash = "#overview";
    });
    toolbar.appendChild(clearBtn);

    /* only appears once something is hidden */
    var showHiddenBtn = BV.el("button", { class: "btn", style: "display:none" }, "show hidden");
    showHiddenBtn.addEventListener("click", function () {
      cmpState.showHidden = !cmpState.showHidden;
      showHiddenBtn.classList.toggle("primary", cmpState.showHidden);
      applyFilter();
    });
    toolbar.appendChild(showHiddenBtn);

    var body = BV.el("div");
    view.appendChild(body);
    body.innerHTML = '<div class="dim" style="padding:.5rem 0">comparing…</div>';

    var data = null;
    var sections = [];   /* [{cat, secEl, bodyEl, rowEls:[{el, r, name}]}] */

    /* persist scroll; self-removes once the report is torn down */
    var onScroll = BV.debounce(function () {
      if (!document.contains(body)) { view.removeEventListener("scroll", onScroll); return; }
      cmpState.scrollTop = view.scrollTop;
    }, 80);
    view.addEventListener("scroll", onScroll);

    swapBtn.addEventListener("click", function () {
      if (!data) return;
      var aPath = data.a.path, bPath = data.b.path;
      body.innerHTML = '<div class="dim" style="padding:.5rem 0">swapping…</div>';
      BV.api.call("open_backup", bPath).then(function (manifest) {
        BV.state.setManifest(manifest);
        return BV.api.call("open_compare", aPath);
      }).then(function (cm) {
        BV.state.compare = cm;
        render(view, toolbar);
      }).catch(function (e) { BV.toast(e.message); });
    });

    BV.api.call("get_compare", cmpState.mode).then(function (res) {
      data = res;
      draw();
    }).catch(function (e) {
      body.innerHTML = '<div class="empty-state"><div class="big">compare failed</div>' +
        '<div class="hint">' + BV.esc(e.message) + "</div></div>";
    });

    function rowMatches(r) {
      if (!cmpState.filter) return true;
      return (r.name + " " + (r.a || "") + " " + (r.b || "")).toLowerCase().indexOf(cmpState.filter) >= 0;
    }

    function updateShowHidden() {
      var n = Object.keys(cmpState.hidden).length;
      showHiddenBtn.style.display = n ? "" : "none";
      showHiddenBtn.textContent = (cmpState.showHidden ? "hide hidden" : "show hidden") + (n ? " (" + n + ")" : "");
    }

    /* filter + hide applied in place - no rebuild, no scroll reset. While a
       filter is active, sections WITH matches open (so matches are visible) and
       empty ones hide; clearing the filter restores your manual expand state. */
    function applyFilter() {
      var filtering = !!cmpState.filter;
      sections.forEach(function (s) {
        var anyVisible = false;
        s.rowEls.forEach(function (re) {
          var isHidden = !!cmpState.hidden[hideKey(s.cat.id, re.name)];
          var vis = rowMatches(re.r) && (!isHidden || cmpState.showHidden);
          re.el.style.display = vis ? "" : "none";
          re.el.classList.toggle("cmp-hidden-row", isHidden && cmpState.showHidden);
          if (vis) anyVisible = true;
        });
        var open = filtering ? anyVisible : !!cmpState.expanded[s.cat.id];
        s.bodyEl.style.display = open ? "" : "none";
        s.hd.querySelector(".sr-cnt").textContent = open ? "▾" : "▸";
        s.secEl.style.display = (filtering && !anyVisible) ? "none" : "";
      });
      updateShowHidden();
    }

    function wireMiniDiff(el, cat) {
      el.addEventListener("click", function () {
        var existing = el.nextElementSibling;
        if (existing && existing.classList.contains("cmp-minidiff")) { existing.remove(); return; }
        var box = BV.el("div", { class: "cmp-minidiff" },
          '<div class="dim" style="padding:.3rem .9rem">loading diff…</div>');
        el.insertAdjacentElement("afterend", box);
        if (cat.id === "pc") {
          var stem = el.dataset.name.replace(/\.PC$/i, "");
          BV.api.call("get_pc_diff_rows", stem, cmpState.mode).then(function (d) {
            var html = '<div class="cmp-minihead"><span class="dim">' + d.total +
              " variable" + (d.total === 1 ? "" : "s") + " differ · " + BV.esc(data.a.robot_name || data.a.name) +
              " vs " + BV.esc(data.b.robot_name || data.b.name) + "</span></div>" + headerRowHtml(data);
            d.rows.forEach(function (r) { html += rowHtml(r, "pc-leaf", false); });
            if (d.truncated) {
              html += '<div class="dim" style="padding:.25rem .9rem;font-size:.75rem">+ ' +
                (d.total - d.rows.length) + " more</div>";
            }
            box.innerHTML = html;
          }).catch(function (e) {
            box.innerHTML = '<div class="dim" style="padding:.3rem .9rem">' + BV.esc(e.message) + "</div>";
          });
          return;
        }
        BV.api.call("get_program_diff_rows", el.dataset.name, cmpState.mode).then(function (d) {
          var fa = encodeURIComponent(d.file_a), fb = encodeURIComponent(d.file_b);
          var html = '<div class="cmp-minihead">' +
            '<a href="#pdiff/' + fa + "/" + fb + '">open full diff ⇄</a>' +
            '<span class="dim">' + d.total_diffs + " differing lines · click a line to open both programs there</span></div>";
          d.rows.forEach(function (r) {
            /* carry WHICH side's line number this is so pdiff lands on the exact
               row - a_n and b_n collide across the aligned rows otherwise */
            var side = r.a_n !== null ? "a" : "b";
            var ln = r.a_n !== null ? r.a_n : r.b_n;
            html += '<div class="pd-row pd-' + r.kind + ' pd-mini" data-ln="' + ln +
              '" data-side="' + side + '">' +
              '<span class="ln">' + (r.a_n !== null ? r.a_n : "") + "</span>" +
              '<span class="pd-text">' + (r.a_text !== null
                ? (r.a_text.trim() ? BV.highlightTP(r.a_text) : "&nbsp;")
                : '<span class="pd-void">— non-existent —</span>') + "</span>" +
              '<span class="ln">' + (r.b_n !== null ? r.b_n : "") + "</span>" +
              '<span class="pd-text">' + (r.b_text !== null
                ? (r.b_text.trim() ? BV.highlightTP(r.b_text) : "&nbsp;")
                : '<span class="pd-void">— non-existent —</span>') + "</span></div>";
          });
          if (d.truncated) {
            html += '<div class="dim" style="padding:.25rem .9rem;font-size:.75rem">+ ' +
              (d.total_diffs - d.rows.length) + ' more — use “open full diff”</div>';
          }
          box.innerHTML = html;
          box.querySelectorAll(".pd-mini").forEach(function (rowEl) {
            rowEl.addEventListener("click", function () {
              location.hash = "#pdiff/" + fa + "/" + fb + "/L" + rowEl.dataset.side + rowEl.dataset.ln;
            });
          });
        }).catch(function (e) {
          box.innerHTML = '<div class="dim" style="padding:.3rem .9rem">' + BV.esc(e.message) + "</div>";
        });
      });
    }

    function draw() {
      if (!data) return;
      body.innerHTML = "";
      sections = [];

      var head = BV.el("div", { class: "cmp-head" });
      if (BV.chrome) BV.chrome.fusedHead = head;   /* fuses with the slab hairline */
      head.innerHTML = sideHtml(data.a, "a") +
        '<span class="cmp-vs">vs</span>' + sideHtml(data.b, "b") +
        '<span class="ov-chips" style="margin-left:auto"><span class="ov-chip">' +
        '<span class="k">differences</span><span class="v">' + data.total + "</span></span></span>";
      body.appendChild(head);

      data.categories.forEach(function (cat) {
        var sec = BV.el("div", { class: "sr-section" });
        var open = !!cmpState.expanded[cat.id];
        var headEl = BV.el("div", { class: "sr-prog" });
        var hd = BV.el("div", { class: "sr-head" },
          '<span class="sr-name">' + BV.esc(cat.label) + "</span>" +
          '<span style="display:flex;gap:.3rem">' + countsHtml(cat) + "</span>" +
          (cat.truncated ? '<span class="dim" style="font-size:.72rem">first ' + cat.rows.length + " shown</span>" : "") +
          '<span class="sr-cnt" style="margin-left:auto">' + (open ? "▾" : "▸") + "</span>");
        headEl.appendChild(hd);
        var bodyEl = BV.el("div", { style: open ? "" : "display:none" });
        headEl.appendChild(bodyEl);

        /* rows built eagerly so filter/hide toggle in place (mini-diff stays lazy) */
        var inner = "";
        if (cat.id === "mastering") {
          inner += '<div class="sr-hit dim"><span class="tx">' +
            (cat.ok
              ? "master counts differ between the robots — that is what you want"
              : "⚠ " + BV.esc(cat.note || "")) + "</span></div>";
        }
        if (cat.rows.length) {
          inner += headerRowHtml(data) + cat.rows.map(function (r) { return rowHtml(r, cat.id, true); }).join("");
        } else if (cat.id !== "mastering") {
          inner += '<div class="sr-hit dim"><span class="tx">no differences</span></div>';
        }
        bodyEl.innerHTML = inner;

        var rowEls = [];
        var rowDivs = bodyEl.querySelectorAll(".cmp-row:not(.cmp-cols-head)");
        cat.rows.forEach(function (r, i) {
          var el = rowDivs[i];
          if (!el) return;
          rowEls.push({ el: el, r: r, name: r.name });
          var hb = el.querySelector(".cmp-hide");
          if (hb) hb.addEventListener("click", function (ev) {
            ev.stopPropagation();   /* don't also trigger the row's mini-diff */
            var hk = hideKey(cat.id, r.name);
            if (cmpState.hidden[hk]) delete cmpState.hidden[hk];
            else cmpState.hidden[hk] = true;
            applyFilter();
          });
        });
        bodyEl.querySelectorAll(".cmp-click").forEach(function (el) { wireMiniDiff(el, cat); });

        hd.addEventListener("click", function () {
          var nowOpen = bodyEl.style.display === "none";
          bodyEl.style.display = nowOpen ? "" : "none";
          hd.querySelector(".sr-cnt").textContent = nowOpen ? "▾" : "▸";
          cmpState.expanded[cat.id] = nowOpen;
        });

        sec.appendChild(headEl);
        body.appendChild(sec);
        sections.push({ cat: cat, secEl: sec, hd: hd, bodyEl: bodyEl, rowEls: rowEls });
      });

      if (data.skipped.length) {
        body.insertAdjacentHTML("beforeend",
          '<div class="dim" style="font-size:.75rem;margin-top:.6rem">not comparable here: ' +
          data.skipped.map(function (sk) {
            return '<span title="' + BV.esc(sk.reason) + '">' + BV.esc(sk.label) + "</span>";
          }).join(" · ") + "</div>");
      }

      applyFilter();
      view.scrollTop = cmpState.scrollTop;   /* restore your place after layout */
    }
  }

  /* once the compare session is loaded (from either source), navigate */
  function landCompare(manifest) {
    BV.state.compare = manifest;
    BV.toast("comparing with " + (manifest.robot_name || manifest.name));
    if (location.hash !== "#compare") location.hash = "#compare";
    else window.dispatchEvent(new HashChangeEvent("hashchange"));
  }

  /* Folder: the file dialog (any backup on disk) */
  function compareFromFolder() {
    BV.api.call("pick_backup_folder").then(function (path) {
      if (!path) return;
      return BV.api.call("open_compare", path).then(landCompare);
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* Library: pick another saved robot (no folder-hunting); opens its Latest as
     the compare session via lib_open(side="b"). The list IS the library tree
     (components/libtree.js) — the same PLANT -> LINE folders as #home, just
     stripped to click-to-pick rows and sized for a modal: every folder still
     collapses (lines too), plants start folded except the open robot's, count
     badges say what's inside the folds, and a filter box finds one robot
     across 71 lines without scrolling for it. */
  function compareFromLibrary() {
    BV.api.call("lib_list").then(function (data) {
      var all = (data && data.robots) || [];
      var curPath = (BV.state.manifest && BV.state.manifest.path) || "";
      var robots = all.filter(function (r) {
        return r.latest_path && r.latest_path !== curPath;
      });
      if (!robots.length) { BV.toast("no other robots saved in the library"); return; }

      /* the robot currently open (it's in the list, just excluded from picking) */
      var cur = all.find(function (r) { return r.latest_path && r.latest_path === curPath; });
      var openPlant = cur ? (cur.plant || "—") : null;

      var modal;
      var tree = BV.libTree({
        counts: true,
        startOpen: function (key, kind) {
          if (kind !== "plant") return true;   /* lines show their robots until folded */
          return openPlant === null || key === openPlant;   /* land on your own plant */
        },
        row: function (r) {
          var row = BV.el("div", { class: "opt-row cmp-pick-row" },
            '<span class="name">' + BV.esc(r.robot || "(unnamed)") +
            (r.model ? '<span class="lib-robot-model">' + BV.esc(r.model) + "</span>" : "") +
            "</span>" +
            ((r.ips && r.ips[0])
              ? '<span class="lib-robot-meta">' + BV.esc(r.ips[0]) + "</span>" : ""));
          row.addEventListener("click", function () {
            modal.close(true);
            BV.api.call("lib_open", r.id, "latest", "b").then(landCompare)
              .catch(function (e) { BV.toast(e.message); });
          });
          return row;
        },
      });

      var body = BV.el("div", { class: "cmp-pick" });
      var treeBody = BV.el("div");
      var sb = BV.searchBox({
        placeholder: "filter robots…",
        onChange: function (q) { paint(q); },
      });
      var search = BV.el("div", { class: "cmp-pick-search" });
      search.appendChild(sb.el);
      body.appendChild(search);
      body.appendChild(treeBody);
      function paint(q) {
        var res = tree.render(treeBody, { robots: robots }, { q: q });
        sb.setCount(q ? res.shown : undefined, res.total);
      }
      paint("");

      modal = BV.modal("compare with…", body, {
        /* Esc (or a stray backdrop click) CLEARS a live filter first; the next
           one closes — the search-box convention everywhere else in the app.
           (The modal's capture-phase Escape swallows the key before the box's
           own handler can, so the clearing has to happen here.) Picking a row
           bypasses this via close(true). */
        beforeClose: function () {
          if (sb.value()) { sb.input.value = ""; paint(""); return false; }
          return true;
        },
      });
      sb.focus();
    }).catch(function (e) { BV.toast(e.message); });
  }

  /* toolbar entry: offer Library (saved robot) or Folder (file dialog).
     The caller passes its button as the menu anchor. */
  BV.compareFlow = function (anchor) {
    BV.menu(anchor, [
      { label: "from library", onClick: compareFromLibrary },
      { label: "from folder", onClick: compareFromFolder },
    ]);
  };

  BV.tabs = BV.tabs || [];
  BV.tabs.push({ id: "compare", label: "compare", hidden: true, always: true, render: render });
})();
